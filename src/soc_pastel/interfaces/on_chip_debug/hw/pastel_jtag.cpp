// SPDX-License-Identifier: MIT
//
// JTAG for simulation: an OpenOCD remote_bitbang server driven from the
// pastel_jtag module. It listens on an ephemeral port (or +pastel_jtag_port)
// and reports the port to the host in the endpoint HELLO as "jtag_port".
// One bitbang command is applied per call, so TCK runs at most at half the
// rate pastel_jtag is clocked.

#include <arpa/inet.h>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <string>
#include <sys/socket.h>
#include <unistd.h>

#include "pastel_sim.h"
#include "svdpi.h"

namespace {
struct Jtag {
  int listen_fd = -1, fd = -1;
  unsigned char buf[4096];
  int len = 0, pos = 0;
  svBit tck = 0, tms = 0, tdi = 0, trst_n = 1;
};
Jtag j;

void set_nonblock(int fd) { fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK); }
}  // namespace

extern "C" {

// Start listening. port 0 picks a free port. Returns the port, or -1.
int pastel_jtag_open(int port) {
  j.listen_fd = socket(AF_INET, SOCK_STREAM, 0);
  int one = 1;
  setsockopt(j.listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  a.sin_port = htons(uint16_t(port));
  if (bind(j.listen_fd, reinterpret_cast<sockaddr*>(&a), sizeof a) != 0 ||
      listen(j.listen_fd, 1) != 0) {
    fprintf(stderr, "pastel_jtag: cannot listen: %s\n", strerror(errno));
    return -1;
  }
  socklen_t al = sizeof a;
  getsockname(j.listen_fd, reinterpret_cast<sockaddr*>(&a), &al);
  set_nonblock(j.listen_fd);
  int actual = ntohs(a.sin_port);
  pastel_sim_info("jtag_port", std::to_string(actual).c_str());
  return actual;
}

void pastel_jtag_tick(svBit tdo, svBit* tck, svBit* tms, svBit* tdi, svBit* trst_n) {
  if (j.fd < 0 && j.listen_fd >= 0) {
    int fd = accept(j.listen_fd, nullptr, nullptr);
    if (fd >= 0) {
      int one = 1;
      setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
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
