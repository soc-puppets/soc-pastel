// SPDX-License-Identifier: MIT
//
// JTAG for simulation: an OpenOCD remote_bitbang server driven from the
// pastel_jtag module. It listens on an ephemeral port (or +pastel_jtag_port)
// and reports the port to the host in the endpoint HELLO as "jtag_port".
// It listens on loopback only, unless +pastel_jtag_bind names another address
// ("0.0.0.0" or "::" for every interface, IPv4 and IPv6), which PASTEL sets
// when the simulator runs on a different machine from OpenOCD. The protocol
// has no authentication, so only do that on a trusted network.
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

// Bind and listen on addr:port; returns the socket or -1.
int listen_on(const char* addr, int port) {
  sockaddr_storage ss{};
  socklen_t len = 0;
  bool any = !strcmp(addr, "0.0.0.0") || !strcmp(addr, "::") || !strcmp(addr, "any");
  int family = AF_INET;
  if (any) {
    // Prefer one dual-stack socket: OpenOCD dials whichever address the
    // simulator's own connection to the host came from.
    family = AF_INET6;
    auto* a6 = reinterpret_cast<sockaddr_in6*>(&ss);
    a6->sin6_family = AF_INET6;
    a6->sin6_addr = in6addr_any;
    a6->sin6_port = htons(uint16_t(port));
    len = sizeof *a6;
  } else {
    auto* a4 = reinterpret_cast<sockaddr_in*>(&ss);
    auto* a6 = reinterpret_cast<sockaddr_in6*>(&ss);
    if (inet_pton(AF_INET, addr, &a4->sin_addr) == 1) {
      a4->sin_family = AF_INET;
      a4->sin_port = htons(uint16_t(port));
      len = sizeof *a4;
    } else if (inet_pton(AF_INET6, addr, &a6->sin6_addr) == 1) {
      family = AF_INET6;
      a6->sin6_family = AF_INET6;
      a6->sin6_port = htons(uint16_t(port));
      len = sizeof *a6;
    } else {
      fprintf(stderr, "pastel_jtag: bad bind address '%s'\n", addr);
      return -1;
    }
  }
  int fd = socket(family, SOCK_STREAM, 0);
  if (fd < 0) return -1;
  int one = 1, zero = 0;
  setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
  if (any) setsockopt(fd, IPPROTO_IPV6, IPV6_V6ONLY, &zero, sizeof zero);
  if (bind(fd, reinterpret_cast<sockaddr*>(&ss), len) != 0 || listen(fd, 1) != 0) {
    int saved = errno;
    close(fd);
    errno = saved;
    return -1;
  }
  return fd;
}

int bound_port(int fd) {
  sockaddr_storage ss{};
  socklen_t len = sizeof ss;
  getsockname(fd, reinterpret_cast<sockaddr*>(&ss), &len);
  return ss.ss_family == AF_INET6 ? ntohs(reinterpret_cast<sockaddr_in6*>(&ss)->sin6_port)
                                  : ntohs(reinterpret_cast<sockaddr_in*>(&ss)->sin_port);
}
}  // namespace

extern "C" {

// Start listening on bind_addr ("" for loopback). port 0 picks a free port.
// Returns the port, or -1.
int pastel_jtag_open(int port, const char* bind_addr) {
  const char* addr = (bind_addr && *bind_addr) ? bind_addr : "127.0.0.1";
  j.listen_fd = listen_on(addr, port);
  if (j.listen_fd < 0 && (!strcmp(addr, "0.0.0.0") || !strcmp(addr, "::"))) {
    j.listen_fd = listen_on("0.0.0.0", port);  // no IPv6 here: IPv4 only
  }
  if (j.listen_fd < 0) {
    fprintf(stderr, "pastel_jtag: cannot listen on %s: %s\n", addr, strerror(errno));
    return -1;
  }
  set_nonblock(j.listen_fd);
  int actual = bound_port(j.listen_fd);
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
