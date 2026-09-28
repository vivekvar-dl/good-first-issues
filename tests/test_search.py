from click.testing import CliRunner

from good_first_issues.graphql.services import (
    extract_search_results,
    filter_issues,
    identify_mode,
    org_user_pipeline,
)
from good_first_issues.main import cli

SAMPLE_ISSUES = [
    {
        "title": "Add REST API endpoint",
        "url": "https://github.com/example/repo/issues/1",
        "body": "Implement the users API.",
        "language": "Python",
    },
    {
        "title": "Fix button alignment",
        "url": "https://github.com/example/repo/issues/2",
        "body": "The login button is off-center.",
        "language": "JavaScript",
    },
    {
        "title": "Improve docs",
        "url": "https://github.com/example/repo/issues/3",
        "body": "Document the public API.",
        "language": "Python",
    },
]


def _titles(issues):
    return [issue["title"] for issue in issues]


def test_filter_language_match():
    result = filter_issues(SAMPLE_ISSUES, language="Python")
    assert _titles(result) == ["Add REST API endpoint", "Improve docs"]


def test_filter_language_match_is_case_insensitive():
    result = filter_issues(SAMPLE_ISSUES, language="python")
    assert _titles(result) == ["Add REST API endpoint", "Improve docs"]


def test_filter_language_miss():
    result = filter_issues(SAMPLE_ISSUES, language="Go")
    assert result == []


def test_filter_language_does_not_substring_match():
    result = filter_issues(SAMPLE_ISSUES, language="Java")
    assert result == []


def test_filter_keyword_match_in_title():
    result = filter_issues(SAMPLE_ISSUES, keyword="button")
    assert _titles(result) == ["Fix button alignment"]


def test_filter_keyword_match_in_body():
    result = filter_issues(SAMPLE_ISSUES, keyword="users")
    assert _titles(result) == ["Add REST API endpoint"]


def test_filter_keyword_match_is_case_insensitive():
    result = filter_issues(SAMPLE_ISSUES, keyword="api")
    assert _titles(result) == ["Add REST API endpoint", "Improve docs"]


def test_filter_keyword_miss():
    result = filter_issues(SAMPLE_ISSUES, keyword="kubernetes")
    assert result == []


def test_filter_combined_match():
    result = filter_issues(SAMPLE_ISSUES, language="Python", keyword="API")
    assert _titles(result) == ["Add REST API endpoint", "Improve docs"]


def test_filter_combined_miss_language():
    result = filter_issues(SAMPLE_ISSUES, language="Go", keyword="API")
    assert result == []


def test_filter_combined_miss_keyword():
    result = filter_issues(SAMPLE_ISSUES, language="JavaScript", keyword="API")
    assert result == []


def test_filter_omitted_flags_leave_results_unchanged():
    result = filter_issues(SAMPLE_ISSUES)
    assert result == SAMPLE_ISSUES


def test_identify_mode_omitted_filters_unchanged():
    _, variables, mode = identify_mode("yankeexe", None, True, False, None, 10)
    assert mode == "user"
    assert (
        variables["searchQuery"]
        == 'user:yankeexe label:"good first issue" is:open is:issue'
    )
    assert "language:" not in variables["searchQuery"]
    assert "in:title,body" not in variables["searchQuery"]


def test_identify_mode_language_filter():
    _, variables, mode = identify_mode(
        "yankeexe", None, True, False, None, 10, language="Python"
    )
    assert mode == "user"
    assert 'language:"Python"' in variables["searchQuery"]
    assert "in:title,body" not in variables["searchQuery"]


def test_identify_mode_keyword_filter():
    _, variables, mode = identify_mode(
        "yankeexe", None, True, False, None, 10, keyword="API"
    )
    assert mode == "user"
    assert '"API" in:title,body' in variables["searchQuery"]
    assert "language:" not in variables["searchQuery"]


def test_identify_mode_combined_filters():
    _, variables, mode = identify_mode(
        "yankeexe",
        None,
        True,
        False,
        None,
        10,
        language="Python",
        keyword="API",
    )
    assert mode == "user"
    assert 'language:"Python"' in variables["searchQuery"]
    assert '"API" in:title,body' in variables["searchQuery"]
    assert variables["searchQuery"].startswith(
        'user:yankeexe label:"good first issue" is:open is:issue'
    )


def test_identify_mode_hacktoberfest_language_only():
    _, variables, mode = identify_mode(
        None, None, False, True, None, 10, language="Python", keyword="API"
    )
    assert mode == "search"
    assert variables["queryString"] == 'topic:hacktoberfest language:"Python"'
    assert "in:title,body" not in variables["queryString"]


def test_org_user_pipeline_extracts_language_and_body():
    payload = {
        "data": {
            "search": {
                "nodes": [
                    {
                        "title": "Add API",
                        "url": "https://example.com/1",
                        "body": "REST API",
                        "repository": {"primaryLanguage": {"name": "Python"}},
                    },
                    {
                        "title": "No language repo",
                        "url": "https://example.com/2",
                        "body": None,
                        "repository": {"primaryLanguage": None},
                    },
                ]
            },
            "rateLimit": {"remaining": 42},
        }
    }
    issues, rate_limit = org_user_pipeline(payload, "user")
    assert rate_limit == 42
    assert issues == [
        {
            "title": "Add API",
            "url": "https://example.com/1",
            "body": "REST API",
            "language": "Python",
        },
        {
            "title": "No language repo",
            "url": "https://example.com/2",
            "body": "",
            "language": "",
        },
    ]


def test_extract_search_results_includes_repo_language():
    payload = {
        "data": {
            "search": {
                "edges": [
                    {
                        "node": {
                            "primaryLanguage": {"name": "JavaScript"},
                            "issues": {
                                "edges": [
                                    {
                                        "node": {
                                            "title": "Fix UI",
                                            "url": "https://example.com/3",
                                            "body": "Button style",
                                        }
                                    }
                                ]
                            },
                        }
                    }
                ]
            },
            "rateLimit": {"remaining": 10},
        }
    }
    issues, rate_limit = extract_search_results(payload)
    assert rate_limit == 10
    assert issues == [
        {
            "title": "Fix UI",
            "url": "https://example.com/3",
            "body": "Button style",
            "language": "JavaScript",
        }
    ]


def test_search_help_documents_language_and_keyword_flags():
    runner = CliRunner()
    result = runner.invoke(cli, ["search", "--help"])
    assert result.exit_code == 0
    assert "--language" in result.output
    assert "-L" in result.output
    assert "--keyword" in result.output
    assert "-k" in result.output
    assert "programming language" in result.output
    assert "title or body" in result.output
    # -l remains bound to --limit
    assert "--limit" in result.output
