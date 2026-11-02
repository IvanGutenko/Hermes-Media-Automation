#!/usr/bin/env python3
"""Expose the local egress proxy to the server's Docker browser, with a small PAC."""
import asyncio
import os
import sys
from pathlib import Path

PAC = b'''function FindProxyForURL(url, host) {
  // Flow and Vids check eligibility through Google account/API endpoints too.
  // Keep their page, authentication, API and media requests on the same egress.
  var domains = ["google.com", "googleapis.com", "gstatic.com", "googleusercontent.com",
                 "googlevideo.com", "googlelabs.com", "labs.google", "goog",
                 "google-analytics.com", "googletagmanager.com",
                 "youtube.com", "youtu.be", "ytimg.com", "youtube-nocookie.com",
                 // Instagram web (login, upload and public readback) needs the Meta CDNs as well.
                 // Instagram is unreachable without the egress; the other services stay DIRECT.
                 "instagram.com", "cdninstagram.com", "instagr.am",
                 "facebook.com", "facebook.net", "fb.com", "fbcdn.net", "fbsbx.com",
                 // TikTok Studio: uploads from a Russian address fail ("Couldn't upload"), so the
                 // page, its API and the ByteDance CDNs share the egress.
                 "tiktok.com", "tiktokv.com", "tiktokv.us", "tiktokcdn.com", "tiktokcdn-us.com",
                 "ttwstatic.com", "byteoversea.com", "ibytedtos.com", "ibyteimg.com", "muscdn.com"];
  for (var i = 0; i < domains.length; i++) {
    if (host == domains[i] || dnsDomainIs(host, "." + domains[i]))
      return "PROXY 127.0.0.1:17891";
  }
  return "DIRECT";
}'''


async def copy(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def handle(reader, writer):
    upstream = None
    try:
        first = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 15)
        if first.startswith(b'GET /browser-proxy.pac '):
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/x-ns-proxy-autoconfig\r\n'
                         b'Cache-Control: no-store\r\nConnection: close\r\nContent-Length: '
                         + str(len(PAC)).encode() + b'\r\n\r\n' + PAC)
            await writer.drain()
            return
        remote, upstream = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', 17893), 10)
        upstream.write(first)
        await upstream.drain()
        await asyncio.gather(copy(reader, upstream), copy(remote, writer))
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        writer.close()
        if upstream:
            upstream.close()


async def main():
    if '--host' in sys.argv:
        # Only the shared directory exposes this socket to the browser container.
        path = Path('/home/gut/hermes-workspace/video_factory/output/.browser-proxy.sock')
        path.unlink(missing_ok=True)
        os.umask(0o077)
        server = await asyncio.start_unix_server(handle, str(path))
    else:
        async def relay(reader, writer):
            upstream = None
            try:
                remote, upstream = await asyncio.open_unix_connection('/data/output/.browser-proxy.sock')
                await asyncio.gather(copy(reader, upstream), copy(remote, writer))
            except OSError:
                pass
            finally:
                writer.close()
                if upstream:
                    upstream.close()
        server = await asyncio.start_server(relay, '127.0.0.1', 17891)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
