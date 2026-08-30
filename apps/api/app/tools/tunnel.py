"""Expose the local API over HTTPS so Razorpay can reach it (§Phase 14).

Razorpay will not POST a webhook to ``localhost``, and it will not POST to
plain HTTP. A Cloudflare quick tunnel gives a public ``*.trycloudflare.com``
URL over TLS with **no account, no card and no configuration** — which matters,
because the whole project is built to run on free tiers.

What this does not do
---------------------

It does not create a *named* tunnel, which would need a Cloudflare login and a
domain. A quick tunnel is anonymous and ephemeral: the URL changes every run.
That is the right trade for a demo and the wrong one for production, so it is
stated here rather than discovered when a webhook stops arriving tomorrow.

The binary is downloaded on demand into a gitignored ``tools/`` directory
rather than committed (55 MB, platform-specific) or installed system-wide
(a hackathon project should not modify a machine it is cloned onto).
"""

from __future__ import annotations

import platform
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"

_ASSETS = {
    ("Windows", "AMD64"): "cloudflared-windows-amd64.exe",
    ("Windows", "ARM64"): "cloudflared-windows-arm64.exe",
    ("Linux", "x86_64"): "cloudflared-linux-amd64",
    ("Linux", "aarch64"): "cloudflared-linux-arm64",
    ("Darwin", "arm64"): "cloudflared-darwin-arm64.tgz",
    ("Darwin", "x86_64"): "cloudflared-darwin-amd64.tgz",
}

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _binary() -> Path:
    name = "cloudflared.exe" if platform.system() == "Windows" else "cloudflared"
    return TOOLS / name


def ensure_binary() -> Path:
    """Download cloudflared if it is not already here."""
    target = _binary()
    if target.exists():
        return target

    asset = _ASSETS.get((platform.system(), platform.machine()))
    if asset is None:
        raise SystemExit(
            f"No cloudflared build known for {platform.system()}/{platform.machine()}. "
            "Install it manually and put it in tools/."
        )
    if asset.endswith(".tgz"):
        raise SystemExit(
            "On macOS install cloudflared with `brew install cloudflared`, then re-run. "
            "The tarball is not unpacked here to keep this script dependency-free."
        )

    TOOLS.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/{asset}"
    print(f"downloading cloudflared ({asset})...")
    urllib.request.urlopen  # noqa: B018 - referenced for clarity in tracebacks
    with urllib.request.urlopen(url, timeout=300) as response, target.open("wb") as out:
        out.write(response.read())
    target.chmod(0o755)
    print(f"  saved to {target}")
    return target


def run(port: int = 8000) -> int:
    """Start a quick tunnel and print the exact steps to register it.

    Streams cloudflared's output so the URL appears as soon as it is assigned,
    then keeps running until interrupted — the tunnel dies with the process,
    which is why the instructions say to leave the window open.
    """
    binary = ensure_binary()
    print(f"\nstarting a Cloudflare quick tunnel to http://localhost:{port} ...\n")

    process = subprocess.Popen(
        [str(binary), "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    public_url: str | None = None
    assert process.stdout is not None
    try:
        for line in process.stdout:
            match = _URL_RE.search(line)
            if match and public_url is None:
                public_url = match.group(0)
                _print_instructions(public_url)
            elif public_url is None:
                # Only echo startup noise until the URL is found; after that the
                # instructions are what matters and cloudflared's heartbeat
                # would scroll them away.
                sys.stdout.write(line)
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        print("\ntunnel closed. The URL is now dead; re-running gives a new one.")
        return 0


def _print_instructions(url: str) -> None:
    webhook_url = f"{url}/api/v1/webhooks/razorpay"
    bar = "=" * 74
    print(
        f"""
{bar}
TUNNEL IS LIVE
{bar}

  Webhook URL — paste this into Razorpay:

      {webhook_url}

  STEPS (about two minutes)

   1. Open  https://dashboard.razorpay.com/app/webhooks
      Make sure the mode toggle top-left says TEST MODE.

   2. Click  + Add New Webhook

   3. Webhook URL:     {webhook_url}

   4. Secret:          paste the value of RAZORPAY_WEBHOOK_SECRET from .env
                       (it must match exactly, or every delivery is rejected)

   5. Active Events — tick these four:
          payment.failed
          payment_link.paid
          invoice.paid
          subscription.charged

   6. Click  Create Webhook

  Then trigger one: create a payment link and pay it with card
  4111 1111 1111 1111, any future expiry, any CVV.

  LEAVE THIS WINDOW OPEN. The tunnel dies with this process, and the URL
  changes every run -- a quick tunnel is anonymous and ephemeral by design.

{bar}
""",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    port = int(args[0]) if args and args[0].isdigit() else 8000
    # A tunnel to a port nothing is listening on produces a 502 for every
    # webhook, which looks like a signature problem and is not.
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/healthz", timeout=3):
            pass
    except Exception:
        print(
            f"! nothing is answering on http://localhost:{port}.\n"
            "  Start the API first (python tasks.py api), then re-run this.\n"
            "  A tunnel to a dead port returns 502 for every delivery, which "
            "looks like a signature failure and is not.",
            file=sys.stderr,
        )
        return 2
    time.sleep(0.1)
    return run(port)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
