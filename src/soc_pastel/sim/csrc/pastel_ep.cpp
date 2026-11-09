// SPDX-License-Identifier: MIT
//
// PASTEL endpoint pump: moves opaque byte messages between testbench VIPs
// (via the DPI functions in pastel_ep_pkg.sv) and one TCP connection to the
// host. See soc_pastel/core/endpoints.py for the wire format.
//
// Single-threaded and non-blocking: everything happens inside DPI calls made
// from the simulation thread, so no simulation time is ever consumed here.

#include <arpa/inet.h>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <string>
#include <sys/socket.h>
#include <unistd.h>
#include <vector>

#include "pastel_sim.h"

namespace {

constexpr uint16_t kControl = 0xFFFF;
constexpr uint32_t kMaxFrame = 16u * 1024u * 1024u;
enum Kind : uint8_t { HELLO = 0, DATA = 1, ERROR = 2, BYE = 3, SYNC = 4, SYNC_ACK = 5 };

// pastel.platform messages
enum PlatformOp : uint8_t { PF_FINISH = 0x01, PF_GET_TIME = 0x02, PF_TIME = 0x82 };

struct Endpoint {
  std::string name, type, path;
  std::deque<std::vector<uint8_t>> rx;
  std::vector<uint8_t> tx;
  std::vector<uint32_t> pending_syncs;
};

struct Pump {
  std::vector<Endpoint> eps;
  int sock = -1;
  bool connected = false, finish = false, lost = false;
  std::vector<uint8_t> in, out;
  std::vector<std::pair<std::string, std::string>> info;  // HELLO "info" entries (JSON values)
  int64_t time_ns = 0;
  int platform_ep = -1;
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
  }
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

// Connect to the host given as "<ip>:<port>:<token>" and send HELLO.
// Returns 0 on success, -1 on failure. An empty spec means "no host".
int pastel_server_connect(const char* spec) {
  Pump& p = pump();
  if (!spec || !*spec) return 0;
  std::string s(spec);
  size_t a = s.find(':'), b = s.find(':', a + 1);
  if (a == std::string::npos || b == std::string::npos) {
    fprintf(stderr, "pastel: bad +pastel_host value '%s'\n", spec);
    return -1;
  }
  std::string host = s.substr(0, a), port = s.substr(a + 1, b - a - 1), token = s.substr(b + 1);

  p.platform_ep = int(p.eps.size());
  p.eps.push_back(Endpoint{"platform", "pastel.platform", "pastel_server", {}, {}, {}});

  addrinfo hints{}, *res = nullptr;
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;
  if (getaddrinfo(host.c_str(), port.c_str(), &hints, &res) != 0 || !res) {
    fprintf(stderr, "pastel: cannot resolve host %s\n", host.c_str());
    return -1;
  }
  p.sock = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
  if (p.sock < 0 || connect(p.sock, res->ai_addr, res->ai_addrlen) != 0) {
    fprintf(stderr, "pastel: cannot connect to host %s:%s: %s\n", host.c_str(), port.c_str(),
            strerror(errno));
    freeaddrinfo(res);
    return -1;
  }
  freeaddrinfo(res);
  int one = 1;
  setsockopt(p.sock, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
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
  return 0;
}

// Service the connection. Returns 0 to continue, 1 if the host asked the
// simulation to finish, 2 if the connection was lost.
int pastel_server_poll(long long time_ns) {
  Pump& p = pump();
  p.time_ns = time_ns;
  if (p.connected) {
    receive();
    flush();
  }
  if (p.finish) return 1;
  if (p.lost) return 2;
  return 0;
}

void pastel_server_close(void) {
  Pump& p = pump();
  if (p.connected) {
    queue_frame(kControl, BYE, nullptr, 0);
    flush();
    close(p.sock);
    p.connected = false;
  }
}

}  // extern "C"
