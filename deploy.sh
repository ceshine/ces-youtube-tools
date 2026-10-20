#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# The build includes members-only guides, so stop unless Cloudflare Access
# still guards the site: an unauthenticated request must be redirected to the
# Access login. See "Access: the site is private" in docs/design.md.
site_url="https://ces-youtube-watching-guides.pages.dev/"
headers=$(curl -sSI --max-time 10 "$site_url") || {
    echo "error: could not reach $site_url; not deploying." >&2
    exit 1
}
if ! grep -qi '^location: https://[^/]*\.cloudflareaccess\.com/' <<<"$headers"; then
    echo "error: $site_url does not redirect to Cloudflare Access; not deploying." >&2
    exit 1
fi

# _site must match pages_build_output_dir in wrangler.toml. --branch=main
# deploys to production, the host name checked above, from any git branch.
uv run ces-youtube-tools site -o _site --include-members-only
npx -y wrangler pages deploy --branch=main --commit-dirty=true
