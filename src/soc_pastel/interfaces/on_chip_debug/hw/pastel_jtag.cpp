// SPDX-License-Identifier: MIT
//
// JTAG for simulation: an OpenOCD remote_bitbang server driven from the
// pastel_jtag module.
//
// By default it listens on a Unix socket in a private directory under
// $TMPDIR (or /tmp), exports the path to child processes as
// PASTEL_JTAG_SOCKET and reports it in the endpoint HELLO as "jtag_socket".
// PASTEL runs OpenOCD as a sidecar of the simulator, so it inherits the
// variable; JTAG never leaves the machine the simulator runs on.
//
// With +pastel_jtag_port=<n> it listens on TCP port n on loopback instead
// (0 picks a free port), reported in HELLO as "jtag_port", for driving a
// model by hand with your own OpenOCD.
//
// One bitbang command is applied per call, so TCK runs at most at half the
// rate pastel_jtag is clocked.

#include <arpa/inet.h>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "pastel_sim.h"
#include "svdpi.h"

namespace {
struct Jtag {
  int listen_fd = -1, fd = -1;
  unsigned char buf[4096];
  int len = 0, pos = 0;
  svBit tck = 0, tms = 0, tdi = 0, trst_n = 1;
  std::string dir, path;  // Unix socket, removed at exit
};
Jtag j;

void set_nonblock(int fd) { fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK); }

void cleanup() {
  if (!j.path.empty()) unlink(j.path.c_str());
  if (!j.dir.empty()) rmdir(j.dir.c_str());
}

std::string json_string(const std::string& s) {
  std::string o = "\"";
  for (char c : s) {
    if (c == '"' || c == '\\') o.push_back('\\');
    o.push_back(c);
  }
  return o + "\"";
}

int open_tcp(int port) {
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  int one = 1;
  setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  a.sin_port = htons(uint16_t(port));
  if (bind(fd, reinterpret_cast<sockaddr*>(&a), sizeof a) != 0 || listen(fd, 1) != 0) {
    fprintf(stderr, "pastel_jtag: cannot listen on port %d: %s\n", port, strerror(errno));
    close(fd);
    return -1;
  }
  socklen_t al = sizeof a;
  getsockname(fd, reinterpret_cast<sockaddr*>(&a), &al);
  pastel_sim_info("jtag_port", std::to_string(ntohs(a.sin_port)).c_str());
  return fd;
}

int open_unix() {
  sockaddr_un a{};
  a.sun_family = AF_UNIX;
  const char* tmp = getenv("TMPDIR");
  for (const char* base : {tmp, "/tmp"}) {
    if (!base || !*base) continue;
    std::string tmpl = std::string(base) + "/pastel-jtag-XXXXXX";
    if (tmpl.size() + 10 >= sizeof a.sun_path) continue;  // room for "/jtag.sock"
    if (mkdtemp(tmpl.data())) {
      j.dir = tmpl;
      break;
    }
  }
  if (j.dir.empty()) {
    fprintf(stderr, "pastel_jtag: cannot create a socket directory: %s\n", strerror(errno));
    return -1;
  }
  j.path = j.dir + "/jtag.sock";
  atexit(cleanup);
  memcpy(a.sun_path, j.path.c_str(), j.path.size() + 1);
  int fd = socket(AF_UNIX, SOCK_STREAM, 0);
  if (bind(fd, reinterpret_cast<sockaddr*>(&a), sizeof a) != 0 || listen(fd, 1) != 0) {
    fprintf(stderr, "pastel_jtag: cannot listen on %s: %s\n", j.path.c_str(), strerror(errno));
    close(fd);
    return -1;
  }
  setenv("PASTEL_JTAG_SOCKET", j.path.c_str(), 1);
  pastel_sim_info("jtag_socket", json_string(j.path).c_str());
  return fd;
}
}  // namespace

extern "C" {

// Start listening: on TCP port `port` on loopback (0 picks one), or on a
// Unix socket if port is negative. Returns 0, or -1 on failure.
int pastel_jtag_open(int port) {
  j.listen_fd = port >= 0 ? open_tcp(port) : open_unix();
  if (j.listen_fd < 0) return -1;
  fcntl(j.listen_fd, F_SETFD, FD_CLOEXEC);
  set_nonblock(j.listen_fd);
  return 0;
}

void pastel_jtag_tick(svBit tdo, svBit* tck, svBit* tms, svBit* tdi, svBit* trst_n) {
  if (j.fd < 0 && j.listen_fd >= 0) {
    int fd = accept(j.listen_fd, nullptr, nullptr);
    if (fd >= 0) {
      int one = 1;
      setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);  // fails harmlessly on Unix
      fcntl(fd, F_SETFD, FD_CLOEXEC);
      set_nonblock(fd);
      j.fd = fd;
    }
  }
  if (j.fd >= 0 && j.pos >= j.len) {
    ssize_t n = recv(j.fd, j.buf, sizeof j.buf, 0);
    if (n > 0) {
      j.len = int(n);
      j.pos = 0;
    } else if (n == 0 || (errno != EAGAIN && errno != EWOULDBLOCK)) {
      close(j.fd);
      j.fd = -1;
    }
  }
  if (j.pos < j.len) {
    unsigned char c = j.buf[j.pos++];
    if (c >= '0' && c <= '7') {
      int v = c - '0';
      j.tck = (v >> 2) & 1;
      j.tms = (v >> 1) & 1;
      j.tdi = v & 1;
    } else if (c == 'R') {
      char r = tdo ? '1' : '0';
      send(j.fd, &r, 1, MSG_NOSIGNAL);
    } else if (c >= 'r' && c <= 'u') {
      j.trst_n = !((c - 'r') & 2);  // r/s: TRST released, t/u: TRST asserted
    } else if (c == 'Q') {
      close(j.fd);
      j.fd = -1;
      j.len = j.pos = 0;
    }
    // 'B'/'b' (blink) and anything else: ignored
  }
  *tck = j.tck;
  *tms = j.tms;
  *tdi = j.tdi;
  *trst_n = j.trst_n;
}

}  // extern "C"
