#!/usr/bin/env python3
"""Commit sahiplik denetleyici.

Repository commit mesajlarindaki istenmeyen ortak-yazar trailer'larini ve
author/committer kimlik ihlallerini raporlar. Komut varsayilan olarak salt
okunurdur; hicbir git referansi veya calisma agacini degistirmez.

Ortak-yazar politikasi (varsayilan):
  - Her turdeki "Co-Authored-By" / "Co-authored by" trailer'i reddedilir.
  - Izinli istisnalar yalniz acik yazili onay ve allowlist ile eklenebilir.
    Allowlist satirlari tam trailer satiriyla birebir eslesmelidir.
  - Author ve committer kimligi yalniz izinli gercek sahiplik listesinde
    olmalidir (varsayilan aday listesi ile birlikte gelir).

Kullanim ornekleri:
  python scripts/check_commit_ownership.py
  python scripts/check_commit_ownership.py --all
  python scripts/check_commit_ownership.py --repo /path/to/repo
  python scripts/check_commit_ownership.py --allowlist-file .git-ownership-allowlist

Cikis kodu:
  0  = ihlal yok
  1  = en az bir ihlal bulundu
  2  = kullanim / ortam hatasi
"""

import argparse
import re
import subprocess
import sys

DENY_TRAILER_RE = re.compile(
    r"(?im)^[ \t]*(?:co-authored-by|co-authored\s+by)[ \t]*:[ \t]*(?P<value>.+)$"
)

# Varsayilan izinli sahiplik (Mehmet KARACAN'in gecerli kimlikleri). CI'da
# ortam oncelikli olarak izinli adi/e-postasi verilebilir.
DEFAULT_ALLOWED_AUTHORS = (
    ("mehmet-karacan", "karacan.mehmet@hotmail.com"),
    ("Mehmet", "karacan.mehmet@hotmail.com"),
    ("Mehmet KARACAN", "karacan.mehmet@hotmail.com"),
)


def git(repo, *args, check=True):
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            "git %s basarisiz (exit=%d): %s"
            % (" ".join(args), proc.returncode, (proc.stderr or "").strip())
        )
    return proc


def load_allowlist(path):
    """Allowlist dosyasindan tam trailer satirlarini okur."""
    allowlist = set()
    if path and _exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.rstrip("\r\n")
                if line and not line.lstrip().startswith("#"):
                    allowlist.add(line.strip())
    return allowlist


def _exists(path):
    from os.path import exists

    return exists(path)


def collect_commits(repo, refspec):
    out = git(repo, "log", refspec, "--format=%H%x09%an%x09%ae%x09%cn%x09%ce").stdout
    commits = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 5:
            commits.append(
                {
                    "sha": parts[0],
                    "author_name": parts[1],
                    "author_email": parts[2],
                    "committer_name": parts[3],
                    "committer_email": parts[4],
                }
            )
    return commits


def full_message(repo, sha):
    return git(repo, "log", "-1", "--format=%B", sha).stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Git repository yolu (default: .)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Tum ref'lerde tara (git log --all); default: HEAD)",
    )
    parser.add_argument(
        "--refspec",
        default=None,
        help="Izlenecek ref/aralik (default: --all ise '--all' yoksa 'HEAD')",
    )
    parser.add_argument(
        "--allowlist-file",
        default=".git-ownership-allowlist",
        help="Izinli tam trailer satirlarinin bulundugu dosya",
    )
    parser.add_argument(
        "--allowed-author",
        action="append",
        default=[],
        help="Izinli '<ad> <email>' kimligi (birden fazla verilebilir)",
    )
    args = parser.parse_args(argv)

    refspec = args.refspec or ("--all" if args.all else "HEAD")

    allowlist = load_allowlist(args.allowlist_file)

    allowed_authors = set(DEFAULT_ALLOWED_AUTHORS)
    for entry in args.allowed_author:
        parts = entry.rsplit(" ", 1)
        if len(parts) != 2:
            print("Gecersiz --allowed-author: %r" % entry, file=sys.stderr)
            return 2
        allowed_authors.add((parts[0], parts[1]))

    try:
        commits = collect_commits(args.repo, refspec)
    except RuntimeError as exc:
        print("HATA: %s" % exc, file=sys.stderr)
        return 2

    violations = []

    for commit in commits:
        sha = commit["sha"]
        author = (commit["author_name"], commit["author_email"])
        committer = (commit["committer_name"], commit["committer_email"])
        message = full_message(args.repo, sha)

        for match in DENY_TRAILER_RE.finditer(message):
            line = match.group(0).strip()
            if line in allowlist:
                continue
            violations.append(
                "%s : reddedilen ortak-yazar trailer'i -> %s" % (sha, line)
            )

        if author not in allowed_authors:
            violations.append(
                "%s : izinli olmayan author -> %s <%s>"
                % (sha, commit["author_name"], commit["author_email"])
            )
        if committer not in allowed_authors:
            violations.append(
                "%s : izinli olmayan committer -> %s <%s>"
                % (sha, commit["committer_name"], commit["committer_email"])
            )

    if violations:
        print("SAHIPLIK IHLALI (%d):" % len(violations))
        for item in violations:
            print("  - %s" % item)
        return 1

    print("OK: commit sahiplik politikasina aykiri kayit yok (%d commit)" % len(commits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
