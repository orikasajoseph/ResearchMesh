"""Bounded public resource lookup. No LLM, arbitrary URLs, clone or execution.

A repository supplied by the user is a selection, NOT a verified paper relation.
Automatic selection requires BOTH the paper title and a stable paper identifier
in the repository README. Missing or rate-limited evidence fails closed.
"""
import base64
from copy import deepcopy
import re
import unicodedata
from urllib.parse import urlparse

import httpx

from input_binding import github_repository_url, paper_ref

SCOPE = "PUBLIC_REPOSITORY_RESOURCE_LOOKUP_NO_LLM"
ARXIV = re.compile(r"(?<![0-9])(?:[0-9]{4}\.[0-9]{4,5}|[a-z-]+/[0-9]{7})(?:v[0-9]+)?(?![0-9])", re.I)
GITHUB = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", re.I)


def normalized(text):
    return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", text).casefold())


def identifiers(paper, search_data):
    urls = [paper.get("url"), paper.get("pdfUrl"), *search_data.get("sourceUrls", [])]
    arxiv = None
    for url in urls:
        parsed = urlparse(url or "")
        if parsed.hostname in {"arxiv.org", "www.arxiv.org"}:
            match = ARXIV.search(parsed.path)
            if match:
                arxiv = re.sub(r"v\d+$", "", match.group(), flags=re.I)
                break
    ids = ([arxiv] if arxiv else []) + ([paper["doi"]] if paper.get("doi") else [])
    return arxiv, ids


class ResourceLookup:
    def __init__(self, client, *, budget=8):
        self.client, self.budget, self.events, self.blocked = client, budget, [], False

    def get(self, url, *, purpose, params=None):
        parsed = urlparse(url)
        # All destinations are constructed here, never followed from remote HTML.
        if (parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "arxiv.org"}
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError("RESOURCE_HOST_NOT_ALLOWED")
        if self.blocked or len(self.events) >= self.budget:
            return None
        event = {"purpose": purpose, "host": parsed.hostname, "path": parsed.path, "httpStatus": None}
        self.events.append(event)
        try:
            with self.client.stream("GET", url, params=params, follow_redirects=False,
                                    headers={"User-Agent": "ResearchMesh-resource-check/0.1"}, timeout=6) as response:
                event["httpStatus"] = response.status_code
                if response.status_code in (403, 429):
                    self.blocked = True
                if response.status_code != 200:
                    return None
                chunks, length = [], 0
                for chunk in response.iter_bytes():
                    length += len(chunk)
                    if length > 262144:
                        event["error"] = "RESPONSE_TOO_LARGE"
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError:
            event["error"] = "RESOURCE_HTTP_UNAVAILABLE"
            return None

    def json(self, url, *, purpose, params=None):
        import json
        body = self.get(url, purpose=purpose, params=params)
        try:
            return json.loads(body) if body else None
        except (ValueError, UnicodeError):
            return None

    def verify(self, url, title, ids):
        if not github_repository_url(url) or not title or not ids:
            return None
        slug = urlparse(url).path.strip("/").removesuffix(".git")
        obj = self.json("https://api.github.com/repos/" + slug + "/readme", purpose="repository_readme")
        if not isinstance(obj, dict) or obj.get("encoding") != "base64":
            return None
        try:
            text = base64.b64decode(obj.get("content", ""), validate=False).decode("utf-8")
        except (ValueError, UnicodeError, TypeError):
            return None
        head = text[:12000]
        if (len(normalized(title)) < 12 or normalized(title) not in normalized(head[:6000])
                or not any(re.search(r"(?<![\w.])" + re.escape(i) + r"(?![\w.])", head, re.I) for i in ids)
                or re.search(r"unofficial|reimplementation|third.party implementation", head[:6000], re.I)):
            return None
        return {"repositoryUrl": "https://github.com/" + slug, "titleMatched": True,
                "identifierMatched": True, "evidenceUrl": "https://github.com/" + slug + "/blob/HEAD/README.md",
                "evidenceKind": "public_github_readme_title_and_paper_identifier",
                "claim": "README contains this title and paper identifier; not proof of official authorship or reproduction."}


def resolve_resource(request, client):
    paper = paper_ref(request["paper"])
    data = request.get("searchData", {})
    explicit = request.get("explicitRepositoryUrls", [])
    base = {"scope": SCOPE, "paper": deepcopy(paper), "request": deepcopy(request), "modelCalls": 0,
            "repositoryUrls": [], "events": [], "verifiedRelation": False}
    if explicit:
        if len(explicit) != 1 or not github_repository_url(explicit[0]):
            raise ValueError("ONE_EXPLICIT_REPOSITORY_REQUIRED")
        return dict(base, repositoryUrls=list(explicit), selectionMode="user_provided", state="selected",
                    warnings=["USER_SELECTED_REPOSITORY_PAPER_RELATION_NOT_VERIFIED"])
    arxiv, ids = identifiers(paper, data)
    if not ids or not paper.get("title"):
        return dict(base, state="unavailable", reason="PAPER_TITLE_AND_IDENTIFIER_REQUIRED")
    lookup = ResourceLookup(client)
    candidates = list(paper.get("repositoryUrls", []))
    candidates += list(data.get("repositoryUrls", []))
    candidates += GITHUB.findall(str(data.get("abstract") or ""))[:2]
    if arxiv:
        page = lookup.get("https://arxiv.org/abs/" + arxiv, purpose="known_paper_links")
        if page:
            # Only candidate extraction; README verifies identity independently.
            candidates += GITHUB.findall(page.decode("utf-8", errors="replace"))[:3]
    checked = set()

    def select(urls):
        for url in urls:
            key = url.lower().rstrip("/").removesuffix(".git")
            if key in checked:
                continue
            checked.add(key)
            proof = lookup.verify(url, paper["title"], ids)
            if proof:
                return dict(base, state="selected", repositoryUrls=[proof["repositoryUrl"]],
                            selectionMode="verified_public_readme", verifiedRelation=True,
                            proof=proof, events=deepcopy(lookup.events), warnings=[])
        return None

    selected = select(candidates[:3])
    if not selected and not lookup.blocked:
        obj = lookup.json("https://api.github.com/search/repositories", purpose="repository_candidates",
                          params={"q": paper["title"].split(":")[0][:120] + " in:name", "per_page": 3})
        if isinstance(obj, dict) and isinstance(obj.get("items"), list):
            selected = select([i.get("html_url", "") for i in obj["items"][:3] if not i.get("fork")])
    return selected or dict(base, state="unavailable", reason="RESOURCE_RATE_LIMITED" if lookup.blocked
                            else "VERIFIED_REPOSITORY_NOT_FOUND", events=deepcopy(lookup.events))


def main(argv=None):
    import argparse
    import json
    from pathlib import Path
    from run_aip_business import save_json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        print("未执行资源解析；没有网络或模型调用。")
        return 0
    if args.output.exists():
        raise ValueError("RESOURCE_RECORD_ALREADY_EXISTS")
    request = json.loads(args.input.read_text(encoding="utf-8-sig"))
    with httpx.Client(trust_env=False) as client:
        result = resolve_resource(request, client)
    save_json(args.output, result)
    print("仓库资源：", result["state"], "；模型调用 0 次。")
    # Missing resource is a recorded business blocker, never guessed by a model.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
