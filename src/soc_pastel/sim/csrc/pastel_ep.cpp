// SPDX-License-Identifier: MIT
//
// PASTEL endpoint pump: moves opaque byte messages between testbench VIPs
// (via the DPI functions in pastel_ep_pkg.sv) and one TCP connection to the
// host. See soc_pastel/core/endpoints.py for the wire format.
//
// Single-threaded and non-blocking: everything happens inside DPI calls made
// from the simulation thread, so no simulation time is ever consumed here.
//
// It also runs *sidecars*: helper processes (OpenOCD) started beside the
// simulator, on the same machine, whose stdin and stdout are relayed to the
// host as an endpoint of type "pastel.pipe". The host lists them in a file
// named by +pastel_sidecars, one per line, tab-separated:
//     <endpoint name> TAB <stderr log file> TAB <argv0> TAB <argv1> ...
// pastel.pipe messages (DATA payloads):
//     host -> sim  u8 0, bytes   write to the sidecar's stdin
//                  u8 1          close its stdin
//     sim -> host  u8 0, bytes   read from its stdout
//                  u8 1, i32     it exited: exit code, or -signal

#include <arpa/inet.h>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <csignal>
#include <fcntl.h>
#include <fstream>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <string>
#include <sys/socket.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif
#include <vector>

#include "pastel_sim.h"

namespace {

constexpr uint16_t kControl = 0xFFFF;
constexpr uint32_t kMaxFrame = 16u * 1024u * 1024u;
enum Kind : uint8_t { HELLO = 0, DATA = 1, ERROR = 2, BYE = 3, SYNC = 4, SYNC_ACK = 5 };

// pastel.platform messages
enum PlatformOp : uint8_t {
  PF_FINISH = 0x01,
  PF_GET_TIME = 0x02,
  PF_SET_WAVES = 0x03,
  PF_TIME = 0x82,
  PF_WAVES = 0x83,
};

struct Endpoint {
  std::string name, type, path;
  std::deque<std::vector<uint8_t>> rx;
  std::vector<uint8_t> tx;
  std::vector<uint32_t> pending_syncs;
};

struct Sidecar {
  std::string name, log;
  std::vector<std::string> argv;
  int ep = -1;
  pid_t pid = -1;
  int in_fd = -1, out_fd = -1;
  std::vector<uint8_t> to_child;
  bool close_in = false, reported = false;
};

struct Pump {
  std::vector<Endpoint> eps;
  std::vector<Sidecar> sidecars;
  int sock = -1;
  bool connected = false, finish = false, lost = false;
  std::vector<uint8_t> in, out;
  std::vector<std::pair<std::string, std::string>> info;  // HELLO "info" entries (JSON values)
  int64_t time_ns = 0;
  int platform_ep = -1;
  bool waves_pausable = false, waves_on = false;
  double finish_deadline = 0;  // wall clock, once finishing: sidecars get until then
};

Pump& pump() {
  static Pump p;
  return p;
}

void put_u16(std::vector<uint8_t>& b, uint16_t v) {
  b.push_back(v & 0xFF);
  b.push_back(v >> 8);
}
void put_u32(std::vector<uint8_t>& b, uint32_t v) {
  for (int i = 0; i < 4; i++) b.push_back((v >> (8 * i)) & 0xFF);
}
uint32_t get_u32(const uint8_t* p) {
  return p[0] | (p[1] << 8) | (p[2] << 16) | (uint32_t(p[3]) << 24);
}

void queue_frame(uint16_t ep, uint8_t kind, const uint8_t* data, size_t len) {
  Pump& p = pump();
  if (!p.connected) return;
  put_u32(p.out, uint32_t(3 + len));
  put_u16(p.out, ep);
  p.out.push_back(kind);
  p.out.insert(p.out.end(), data, data + len);
}

void flush() {
  Pump& p = pump();
  while (p.connected && !p.out.empty()) {
    ssize_t n = send(p.sock, p.out.data(), p.out.size(), MSG_NOSIGNAL);
    if (n > 0) {
      p.out.erase(p.out.begin(), p.out.begin() + n);
    } else if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
      return;  // try again on the next poll
    } else {
      p.connected = false;
      p.lost = true;
    }
  }
}

std::string json_escape(const std::string& s) {
  std::string o;
  for (char c : s) {
    if (c == '"' || c == '\\') o.push_back('\\');
    if (static_cast<unsigned char>(c) >= 0x20) o.push_back(c);
  }
  return o;
}

void ack_syncs_if_drained(int h) {
  Endpoint& e = pump().eps[h];
  if (!e.rx.empty()) return;
  for (uint32_t seq : e.pending_syncs) {
    std::vector<uint8_t> b;
    put_u32(b, seq);
    queue_frame(uint16_t(h), SYNC_ACK, b.data(), b.size());
  }
  e.pending_syncs.clear();
}

void handle_platform(const std::vector<uint8_t>& m) {
  Pump& p = pump();
  if (m.empty()) return;
  if (m[0] == PF_FINISH) {
    p.finish = true;
  } else if (m[0] == PF_GET_TIME) {
    std::vector<uint8_t> b{PF_TIME};
    for (int i = 0; i < 8; i++) b.push_back((uint64_t(p.time_ns) >> (8 * i)) & 0xFF);
    queue_frame(uint16_t(p.platform_ep), DATA, b.data(), b.size());
  } else if (m[0] == PF_SET_WAVES && m.size() >= 2) {
    if (p.waves_pausable) p.waves_on = m[1] != 0;
    const uint8_t b[] = {PF_WAVES, uint8_t(p.waves_on)};
    queue_frame(uint16_t(p.platform_ep), DATA, b, sizeof b);
  }
}

// --- Sidecars ----------------------------------------------------------------

enum PipeOp : uint8_t { PIPE_DATA = 0, PIPE_CLOSE = 1 };

Sidecar* sidecar_for(int ep) {
  for (Sidecar& sc : pump().sidecars)
    if (sc.ep == ep) return &sc;
  return nullptr;
}

std::vector<std::string> split_tabs(const std::string& line) {
  std::vector<std::string> out;
  size_t start = 0;
  for (;;) {
    size_t tab = line.find('\t', start);
    out.push_back(line.substr(start, tab - start));
    if (tab == std::string::npos) return out;
    start = tab + 1;
  }
}

// Read the sidecar list and add an endpoint for each. Before HELLO.
bool load_sidecars(const char* file) {
  Pump& p = pump();
  if (!file || !*file) return true;
  std::ifstream in(file);
  if (!in) {
    fprintf(stderr, "pastel: cannot read sidecar list %s\n", file);
    return false;
  }
  std::string line;
  while (std::getline(in, line)) {
    if (line.empty()) continue;
    std::vector<std::string> f = split_tabs(line);
    if (f.size() < 3) {
      fprintf(stderr, "pastel: bad sidecar line in %s: %s\n", file, line.c_str());
      return false;
    }
    Sidecar sc;
    sc.name = f[0];
    sc.log = f[1];
    sc.argv.assign(f.begin() + 2, f.end());
    sc.ep = int(p.eps.size());
    p.eps.push_back(Endpoint{sc.name, "pastel.pipe", "pastel_server", {}, {}, {}});
    p.sidecars.push_back(std::move(sc));
  }
  return true;
}

void spawn(Sidecar& sc) {
  int to_child[2], from_child[2];
  if (pipe(to_child) != 0 || pipe(from_child) != 0) {
    fprintf(stderr, "pastel: sidecar %s: pipe: %s\n", sc.name.c_str(), strerror(errno));
    return;
  }
  pid_t pid = fork();
  if (pid == 0) {
#ifdef __linux__
    prctl(PR_SET_PDEATHSIG, SIGKILL);  // never outlive the simulator
#endif
    signal(SIGPIPE, SIG_DFL);
    dup2(to_child[0], 0);
    dup2(from_child[1], 1);
    int log = open(sc.log.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (log >= 0) dup2(log, 2);
    for (int fd = 3; fd < 1024; fd++) close(fd);  // not the host connection or JTAG socket
    std::vector<char*> argv;
    for (std::string& a : sc.argv) argv.push_back(a.data());
    argv.push_back(nullptr);
    execvp(argv[0], argv.data());
    fprintf(stderr, "pastel: cannot run %s: %s\n", argv[0], strerror(errno));
    _exit(127);
  }
  close(to_child[0]);
  close(from_child[1]);
  if (pid < 0) {
    fprintf(stderr, "pastel: sidecar %s: fork: %s\n", sc.name.c_str(), strerror(errno));
    close(to_child[1]);
    close(from_child[0]);
    return;
  }
  sc.pid = pid;
  sc.in_fd = to_child[1];
  sc.out_fd = from_child[0];
  fcntl(sc.in_fd, F_SETFL, fcntl(sc.in_fd, F_GETFL) | O_NONBLOCK);
  fcntl(sc.out_fd, F_SETFL, fcntl(sc.out_fd, F_GETFL) | O_NONBLOCK);
  fcntl(sc.in_fd, F_SETFD, FD_CLOEXEC);
  fcntl(sc.out_fd, F_SETFD, FD_CLOEXEC);
}

void report_exit(Sidecar& sc, int status) {
  int32_t code = WIFEXITED(status) ? WEXITSTATUS(status) : -WTERMSIG(status);
  std::vector<uint8_t> b{PIPE_CLOSE};
  put_u32(b, uint32_t(code));
  queue_frame(uint16_t(sc.ep), DATA, b.data(), b.size());
  sc.reported = true;
}

void service_sidecar(Sidecar& sc) {
  if (sc.in_fd >= 0) {
    while (!sc.to_child.empty()) {
      ssize_t n = write(sc.in_fd, sc.to_child.data(), sc.to_child.size());
      if (n > 0) {
        sc.to_child.erase(sc.to_child.begin(), sc.to_child.begin() + n);
      } else if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
        break;
      } else {  // the sidecar closed its stdin
        sc.to_child.clear();
        sc.close_in = true;
      }
    }
    if (sc.close_in && sc.to_child.empty()) {
      close(sc.in_fd);
      sc.in_fd = -1;
    }
  }
  if (sc.out_fd >= 0) {
    uint8_t buf[65536];
    buf[0] = PIPE_DATA;
    ssize_t n = read(sc.out_fd, buf + 1, sizeof buf - 1);
    if (n > 0) {
      queue_frame(uint16_t(sc.ep), DATA, buf, size_t(n) + 1);
    } else if (n == 0 || (errno != EAGAIN && errno != EWOULDBLOCK)) {
      close(sc.out_fd);
      sc.out_fd = -1;
    }
  }
  // Report the exit once its output has all been relayed.
  if (sc.pid > 0 && sc.out_fd < 0 && !sc.reported) {
    int status = 0;
    if (waitpid(sc.pid, &status, WNOHANG) == sc.pid) {
      sc.pid = -1;
      report_exit(sc, status);
    }
  }
}

double now_s() {
  timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return ts.tv_sec + ts.tv_nsec * 1e-9;
}

bool sidecars_running() {
  for (const Sidecar& sc : pump().sidecars)
    if (sc.pid > 0) return true;
  return false;
}

void stop_sidecars() {
  for (Sidecar& sc : pump().sidecars) {
    if (sc.in_fd >= 0) close(sc.in_fd);  // OpenOCD exits when its Tcl pipe closes
    if (sc.out_fd >= 0) close(sc.out_fd);
    sc.in_fd = sc.out_fd = -1;
    if (sc.pid <= 0) continue;
    // Normally gone by now (see pastel_server_poll). Anything left can't be
    // waited for: with the simulation stopped, it may be stuck on JTAG.
    int status;
    if (waitpid(sc.pid, &status, WNOHANG) == 0) {
      kill(sc.pid, SIGKILL);
      waitpid(sc.pid, &status, 0);
    }
    sc.pid = -1;
  }
}

void handle_pipe(Sidecar& sc, const std::vector<uint8_t>& m) {
  if (m.empty()) return;
  if (m[0] == PIPE_DATA && !sc.close_in) {
    sc.to_child.insert(sc.to_child.end(), m.begin() + 1, m.end());
  } else if (m[0] == PIPE_CLOSE) {
    sc.close_in = true;
  }
  service_sidecar(sc);
}

void dispatch(uint16_t ep, uint8_t kind, std::vector<uint8_t> payload) {
  Pump& p = pump();
  if (ep == kControl) {
    if (kind == BYE) p.finish = true;
    if (kind == ERROR) {
      fprintf(stderr, "pastel: host error: %.*s\n", int(payload.size()),
              reinterpret_cast<const char*>(payload.data()));
    }
    return;
  }
  if (ep >= p.eps.size()) {
    fprintf(stderr, "pastel: frame for unknown endpoint %u\n", ep);
    return;
  }
  if (kind == DATA) {
    if (int(ep) == p.platform_ep) {
      handle_platform(payload);
    } else if (Sidecar* sc = sidecar_for(ep)) {
      handle_pipe(*sc, payload);
    } else {
      p.eps[ep].rx.push_back(std::move(payload));
    }
  } else if (kind == SYNC && payload.size() == 4) {
    p.eps[ep].pending_syncs.push_back(get_u32(payload.data()));
    ack_syncs_if_drained(ep);
  }
}

void receive() {
  Pump& p = pump();
  uint8_t buf[65536];
  for (;;) {
    ssize_t n = recv(p.sock, buf, sizeof buf, 0);
    if (n > 0) {
      p.in.insert(p.in.end(), buf, buf + n);
    } else if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
      break;
    } else {
      p.connected = false;
      p.lost = true;
      break;
    }
  }
  size_t off = 0;
  while (p.in.size() - off >= 4) {
    uint32_t len = get_u32(&p.in[off]);
    if (len < 3 || len > kMaxFrame) {
      fprintf(stderr, "pastel: bad frame length %u from host\n", len);
      p.connected = false;
      p.lost = true;
      return;
    }
    if (p.in.size() - off < 4 + len) break;
    uint16_t ep = p.in[off + 4] | (p.in[off + 5] << 8);
    uint8_t kind = p.in[off + 6];
    std::vector<uint8_t> payload(p.in.begin() + off + 7, p.in.begin() + off + 4 + len);
    dispatch(ep, kind, std::move(payload));
    off += 4 + len;
  }
  p.in.erase(p.in.begin(), p.in.begin() + off);
}

// Connect to one resolved address, giving up after timeout_ms. Returns the
// socket (blocking) or -1 with errno set.
int connect_with_timeout(const addrinfo* ai, int timeout_ms) {
  int fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
  if (fd < 0) return -1;
  int flags = fcntl(fd, F_GETFL);
  fcntl(fd, F_SETFL, flags | O_NONBLOCK);
  int rc = connect(fd, ai->ai_addr, ai->ai_addrlen);
  if (rc != 0 && errno == EINPROGRESS) {
    pollfd pfd{fd, POLLOUT, 0};
    rc = poll(&pfd, 1, timeout_ms);
    if (rc == 0) {
      errno = ETIMEDOUT;
      rc = -1;
    } else if (rc > 0) {
      int err = 0;
      socklen_t len = sizeof err;
      getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len);
      errno = err;
      rc = err ? -1 : 0;
    }
  }
  if (rc != 0) {
    int saved = errno;
    close(fd);
    errno = saved;
    return -1;
  }
  fcntl(fd, F_SETFL, flags);
  return fd;
}

void tune_socket(int fd) {
  int one = 1;
  setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
  // Keepalives, so an idle connection through a firewall or NAT survives a
  // long stretch of simulation with no endpoint traffic.
  setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &one, sizeof one);
#ifdef TCP_KEEPIDLE
  int idle = 60, intvl = 15, cnt = 8;
  setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, &idle, sizeof idle);
  setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &intvl, sizeof intvl);
  setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, &cnt, sizeof cnt);
#endif
}

// Dial the host, trying every address it resolves to (IPv6 and IPv4), and
// retrying for a while in case the network or DNS is briefly unavailable.
int dial(const std::string& host, const std::string& port) {
  constexpr int kAttempts = 10, kTimeoutMs = 5000, kRetryDelayUs = 1000000;
  for (int attempt = 1; attempt <= kAttempts; attempt++) {
    addrinfo hints{}, *res = nullptr;
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int gai = getaddrinfo(host.c_str(), port.c_str(), &hints, &res);
    if (gai != 0) {
      fprintf(stderr, "pastel: cannot resolve host %s: %s\n", host.c_str(), gai_strerror(gai));
    } else {
      for (addrinfo* ai = res; ai; ai = ai->ai_next) {
        int fd = connect_with_timeout(ai, kTimeoutMs);
        if (fd >= 0) {
          freeaddrinfo(res);
          return fd;
        }
        char addr[NI_MAXHOST] = "?";
        getnameinfo(ai->ai_addr, ai->ai_addrlen, addr, sizeof addr, nullptr, 0, NI_NUMERICHOST);
        fprintf(stderr, "pastel: cannot connect to %s (%s) port %s: %s\n", host.c_str(), addr,
                port.c_str(), strerror(errno));
      }
      freeaddrinfo(res);
    }
    if (attempt < kAttempts) usleep(kRetryDelayUs);
  }
  return -1;
}

}  // namespace

void pastel_sim_info(const char* key, const char* json_value) {
  pump().info.emplace_back(key, json_value);
}

extern "C" {

int pastel_ep_open(const char* path, const char* ep_type, const char* name) {
  Pump& p = pump();
  if (p.connected) {
    fprintf(stderr, "pastel: endpoint %s opened after the host connected; it was ignored\n", name);
    return -1;
  }
  p.eps.push_back(Endpoint{name, ep_type, path, {}, {}, {}});
  return int(p.eps.size()) - 1;
}

int pastel_ep_rx_len(int h) {
  Pump& p = pump();
  if (h < 0 || h >= int(p.eps.size()) || p.eps[h].rx.empty()) return 0;
  return int(p.eps[h].rx.front().size());
}

char pastel_ep_rx_byte(int h, int i) {
  Pump& p = pump();
  if (h < 0 || h >= int(p.eps.size()) || p.eps[h].rx.empty()) return 0;
  const auto& m = p.eps[h].rx.front();
  return (i >= 0 && size_t(i) < m.size()) ? char(m[i]) : 0;
}

void pastel_ep_rx_pop(int h) {
  Pump& p = pump();
  if (h < 0 || h >= int(p.eps.size()) || p.eps[h].rx.empty()) return;
  p.eps[h].rx.pop_front();
  ack_syncs_if_drained(h);
}

void pastel_ep_tx_begin(int h) {
  if (h >= 0 && h < int(pump().eps.size())) pump().eps[h].tx.clear();
}

void pastel_ep_tx_byte(int h, char b) {
  if (h >= 0 && h < int(pump().eps.size())) pump().eps[h].tx.push_back(uint8_t(b));
}

void pastel_ep_tx_end(int h) {
  Pump& p = pump();
  if (h < 0 || h >= int(p.eps.size())) return;
  queue_frame(uint16_t(h), DATA, p.eps[h].tx.data(), p.eps[h].tx.size());
  p.eps[h].tx.clear();
  flush();
}

// Connect to the host given as "<host>:<port>:<token>", send HELLO, and start
// the sidecars listed in the file `sidecars` (may be empty). <host> is a
// name, an IPv4 address or a bracketed IPv6 address ("[fd00::1]"). Returns 0
// on success, -1 on failure. An empty spec means "no host".
int pastel_server_connect(const char* spec, const char* sidecars) {
  Pump& p = pump();
  if (!spec || !*spec) return 0;
  std::string s(spec);
  // Split from the right: the token and port contain no ':', a host may.
  size_t b = s.rfind(':');
  size_t a = (b == std::string::npos || b == 0) ? std::string::npos : s.rfind(':', b - 1);
  if (a == std::string::npos || a == 0) {
    fprintf(stderr, "pastel: bad +pastel_host value '%s'\n", spec);
    return -1;
  }
  std::string host = s.substr(0, a), port = s.substr(a + 1, b - a - 1), token = s.substr(b + 1);
  if (host.size() >= 2 && host.front() == '[' && host.back() == ']')
    host = host.substr(1, host.size() - 2);

  p.platform_ep = int(p.eps.size());
  p.eps.push_back(Endpoint{"platform", "pastel.platform", "pastel_server", {}, {}, {}});
  if (!load_sidecars(sidecars)) return -1;

  p.sock = dial(host, port);
  if (p.sock < 0) {
    fprintf(stderr, "pastel: giving up on host %s port %s\n", host.c_str(), port.c_str());
    return -1;
  }
  tune_socket(p.sock);
  fcntl(p.sock, F_SETFL, fcntl(p.sock, F_GETFL) | O_NONBLOCK);
  p.connected = true;

  std::string j = "{\"version\":1,\"token\":\"" + json_escape(token) + "\",\"endpoints\":[";
  for (size_t i = 0; i < p.eps.size(); i++) {
    const Endpoint& e = p.eps[i];
    if (i) j += ",";
    j += "{\"id\":" + std::to_string(i) + ",\"name\":\"" + json_escape(e.name) + "\",\"type\":\"" +
         json_escape(e.type) + "\",\"path\":\"" + json_escape(e.path) + "\"}";
  }
  j += "],\"info\":{";
  for (size_t i = 0; i < p.info.size(); i++) {
    if (i) j += ",";
    j += "\"" + json_escape(p.info[i].first) + "\":" + p.info[i].second;
  }
  j += "}}";
  queue_frame(kControl, HELLO, reinterpret_cast<const uint8_t*>(j.data()), j.size());
  flush();
  if (!p.sidecars.empty()) signal(SIGPIPE, SIG_IGN);  // a sidecar's stdin may close
  for (Sidecar& sc : p.sidecars) spawn(sc);
  return 0;
}

// Service the connection. Returns 0 to continue, 1 if the host asked the
// simulation to finish, 2 if the connection was lost.
//
// Once asked to finish, the simulation keeps running until every sidecar has
// exited (at most a few seconds): OpenOCD may still drive JTAG while it
// shuts down, and that needs the simulation to keep ticking.
int pastel_server_poll(long long time_ns) {
  Pump& p = pump();
  p.time_ns = time_ns;
  if (p.connected) receive();
  for (Sidecar& sc : p.sidecars) service_sidecar(sc);
  if (p.connected) flush();
  if (p.finish) {
    if (p.finish_deadline == 0) {
      p.finish_deadline = now_s() + 5.0;
      for (Sidecar& sc : p.sidecars) sc.close_in = true;  // OpenOCD exits on EOF
    }
    return (sidecars_running() && now_s() < p.finish_deadline) ? 0 : 1;
  }
  if (p.lost) return 2;
  return 0;
}

// Waves. pastel_server calls _init once it has opened a waveform file and is
// dumping. If the simulator can pause dumping, the host's SET_WAVES requests
// then set the state that _waves_on reports, and pastel_server follows it;
// otherwise dumping stays on and SET_WAVES is answered with "still on".
void pastel_server_waves_init(int can_pause) {
  pump().waves_pausable = can_pause != 0;
  pump().waves_on = true;
}

int pastel_server_waves_on(void) { return pump().waves_on; }

void pastel_server_close(void) {
  Pump& p = pump();
  stop_sidecars();
  if (p.connected) {
    queue_frame(kControl, BYE, nullptr, 0);
    flush();
    close(p.sock);
    p.connected = false;
  }
}

}  // extern "C"
