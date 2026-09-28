"""Services for GraphQL mode"""

import sys
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Union

import requests
from halo import Halo
from requests.adapters import HTTPAdapter
from requests.models import Response
from rich.console import Console
from urllib3.util.retry import Retry

from good_first_issues.graphql.queries import core_query, search_query

# Initializations
console = Console(color_system="auto")
spinner: Halo = Halo(text="Looking for good first issues...", spinner="dots")

# Type Aliases
ExtractedRepoIssues = Tuple[List[Tuple[Optional[str], Optional[str]]], int]


# Custom Error Class.
class NoToken(Exception):
    pass


def org_user_pipeline(payload: Dict, mode: str) -> Tuple[Iterable, int]:
    """
    Extract issues related to organization or a user.
    """
    base_data: List = payload.get("data").get("search").get("nodes")

    # Extract rate limit value.
    rate_limit: int = payload["data"].get("rateLimit").get("remaining")

    spinner.start()

    issues = [_issue_record(data, data.get("repository")) for data in base_data]

    spinner.succeed("Search Complete.")

    return issues, rate_limit


def get_base_issues(data: List) -> Iterator[Tuple[List, Optional[str]]]:
    """
    Get the edge that connects to the issue nodes, plus repo language.
    """
    for item in data:
        node = item.get("node") or {}
        edges = (node.get("issues") or {}).get("edges")
        language_name = _repo_language(node)

        # Remove empty list.
        if edges:
            yield edges, language_name


def get_issues(issues: Iterator[Tuple[List, Optional[str]]]) -> Iterator[Dict]:
    """
    Extracts issue title, URL, body, and repository language from the payload.
    """
    for edges, language_name in issues:
        for issue in edges:
            record = _issue_record(issue.get("node") or {}, language=language_name)
            yield record


def extract_repo_issues(
    payload: Dict,
) -> ExtractedRepoIssues:
    """
    Extract issues with repo name specified.
    """
    # Type Aliases
    BaseData = Optional[Iterable[Dict[str, Dict[str, str]]]]
    IssueData = Tuple[Optional[str], Optional[str]]

    base_data: BaseData = payload["data"].get("repository").get("issues").get("edges")

    rate_limit: int = payload["data"].get("rateLimit").get("remaining")

    issues = []

    if base_data:
        for issue in base_data:
            issue_data: IssueData = (
                issue["node"].get("title"),
                issue["node"].get("url"),
            )

            issues.append(issue_data)

    return issues, rate_limit


def extract_search_results(payload: Dict) -> Tuple[Iterable, int]:
    """
    Extract issues based on search query.
    """
    # Get the edges connecting to all the repositories.
    base_data: List = payload["data"].get("search").get("edges")

    # Extract rate limit value.
    rate_limit: int = payload["data"].get("rateLimit").get("remaining")

    spinner.start()

    # Generator pipeline: Extract issue title and url.
    pipeline: Iterable = get_issues(get_base_issues(base_data))

    spinner.succeed("Search Complete.")

    return list(pipeline), rate_limit


def _repo_language(repository: Optional[Dict]) -> str:
    """Return the repository primary language name, if present."""
    if not repository:
        return ""

    primary = repository.get("primaryLanguage")
    if not primary:
        return ""

    return primary.get("name") or ""


def _issue_record(
    node: Optional[Dict],
    repository: Optional[Dict] = None,
    language: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Build a normalized issue record from a GraphQL issue node."""
    node = node or {}
    repo = repository if repository is not None else node.get("repository")
    return {
        "title": node.get("title"),
        "url": node.get("url"),
        "body": node.get("body") or "",
        "language": language if language is not None else _repo_language(repo),
    }


def _append_search_filters(
    query: str, language: Optional[str], keyword: Optional[str]
) -> str:
    """Append optional language and keyword qualifiers to a GitHub search query."""
    if language:
        query = f'{query} language:"{language}"'

    if keyword:
        sanitized_keyword = keyword.replace('"', "")
        query = f'{query} "{sanitized_keyword}" in:title,body'

    return query


def filter_issues(
    issues: Optional[Iterable],
    language: Optional[str] = None,
    keyword: Optional[str] = None,
) -> Optional[List]:
    """Filter issues by repository language and/or keyword.

    Language is matched against the repository programming language
    (GitHub primaryLanguage), case-insensitive. Keyword is matched against
    the issue title or body, case-insensitive. Omitted filters leave results
    unchanged. Both filters can be combined.
    """
    if not issues or (not language and not keyword):
        return list(issues) if issues is not None else issues

    language_key = language.lower() if language else None
    keyword_key = keyword.lower() if keyword else None

    filtered: List = []
    for issue in issues:
        if isinstance(issue, dict):
            title = issue.get("title") or ""
            body = issue.get("body") or ""
            repo_language = issue.get("language") or ""
        else:
            title = issue[0] or ""
            body = issue[2] if len(issue) > 2 and issue[2] else ""
            repo_language = issue[3] if len(issue) > 3 and issue[3] else ""

        if language_key and repo_language.lower() != language_key:
            continue

        haystack = f"{title}\n{body}".lower()
        if keyword_key and keyword_key not in haystack:
            continue

        filtered.append(issue)

    return filtered


def identify_mode(
    name: str,
    repo: str,
    user: bool,
    hacktoberfest: bool,
    period: str,
    limit: int,
    language: Optional[str] = None,
    keyword: Optional[str] = None,
) -> Tuple[str, Dict, str]:
    """
    Identify the mode based on arguments passed.

    Used for selecting:
    1. query to use
    2. variables for the query
    3. function(mode) to pass the above values to
    """
    variables: Dict = {"limit": limit}

    base_variable = 'label:"good first issue" is:open is:issue'

    if period:
        base_variable = f"{base_variable} created:>={period}"

    # Issue search: language uses GitHub's repo language field; keyword
    # matches title or body. Hacktoberfest is a repository search, so
    # keyword is applied client-side after issues are fetched.
    base_variable = _append_search_filters(base_variable, language, keyword)

    if name and user and repo:
        # If CLI gets the --user flag along with the --repo flag, look into that particular repo.
        query = core_query
        variables["searchQuery"] = f"repo:{name}/{repo} {base_variable}"
        mode: str = "repo"

    elif name and repo:
        # If CLI gets the --repo flag, looking into that particular repo.
        query = core_query
        variables["searchQuery"] = f"repo:{name}/{repo} {base_variable}"
        mode = "repo"

    elif name and user:
        # If CLI gets --user flag, looks into user repos.
        query = core_query
        variables["searchQuery"] = f"user:{name} {base_variable}"
        mode = "user"

    elif hacktoberfest:
        # If hacktoberfest flag is passed, get the repos with topic hacktoberfest and their issues.
        query = search_query
        search_query_var = "topic:hacktoberfest"
        if period:
            search_query_var = f"{search_query_var} created:>={period}"
        if language:
            search_query_var = f'{search_query_var} language:"{language}"'
        variables["queryString"] = search_query_var
        mode = "search"

    else:
        # if CLI gets not flag, defaults to looking into org repos.
        query = core_query
        variables["searchQuery"] = f"org:{name} {base_variable}"
        mode = "org"

    return query, variables, mode


def caller(token: Union[str, bool], query: str, variables: Dict) -> Dict:
    """
    Call the GitHub GraphQL API.

    Retries if the status codes on `status_forcelist` is returned
    from the server.

    > Centralized requests handler, all network exceptions captured here.
    """
    try:
        request_headers: Dict[str, str] = dict()

        if not token:
            raise NoToken()
        else:
            request_headers["Authorization"] = f"token {token}"

        s = requests.Session()

        # Retry factors
        retries = Retry(
            total=5,
            backoff_factor=0.3,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=(["POST"]),
        )

        s.mount("https://", HTTPAdapter(max_retries=retries))

        # API Call
        response: Response = s.post(
            "https://api.github.com/graphql",
            headers=request_headers,
            json={
                "query": query,
                "variables": variables,
            },
            timeout=20,
        )

        # Check for erros in GraphQL response.
        if "errors" in response.json():
            raise Exception()

        response.raise_for_status()

    except requests.exceptions.ReadTimeout:
        spinner.fail("Error")
        console.print("Network connection timeout.:construction:", style="bold red")

        sys.exit()
    except requests.exceptions.HTTPError:
        spinner.fail("Error")
        error = response.json().get("message")
        console.print(
            f"Error: {error}.:x:",
            style="bold red",
        )

        sys.exit()
    except NoToken:
        spinner.fail("Error")
        console.print(
            "No GitHub Token found. Use `gfi config` to enter your token.:key:",
            style="bold red",
        )
        console.print(
            "> https://docs.github.com/en/github/authenticating-to-github/creating-a-personal-access-token"  # noqa: E501
        )

        sys.exit()
    except Exception:
        spinner.fail("Error")
        error_base = response.json().get("errors")[0]

        console.print(
            f"Error: {error_base.get('message')}:x:",
            style="bold red",
        )

        sys.exit()
    # ruff: noqa: E722
    except:
        spinner.fail("Error")
        console.print(
            "An error has occurred. Please try again later or open an issue on GitHub.:x:",  # noqa: E501
            style="bold red",
        )

        sys.exit()

    return response.json()
