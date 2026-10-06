"""
The AI Analyst Route is provider-agnostic (Master Architecture §16, owner decision 2026-10-06): the provider and model
are configuration behind a provider adapter, never an architectural dependency. Offline, static checks that keep it so
before the analyst exists, and after:

    provider    no production module (worker/, ops/, the repository root) imports a hosted-LLM provider SDK
                (directly or through importlib) or names a provider API host, except inside a provider-adapter package
                listed in PROVIDER_ADAPTER_PACKAGES. The list is EMPTY: no adapter exists yet. The AI analyst phase
                adds its adapter package here, in the same reviewed change, and nothing else may call a provider
                directly.
    secrets     no migration defines a column for a provider credential (§16: secrets are server configuration only,
                never financial-data records).
    rule        the Master Architecture keeps the provider-agnostic rule (§16) and the provider/model provenance (§17).

A comment or docstring that names a provider is prose, not a dependency (frozen F6.3 / F6.4 modules state that they
read no Gemini output), and is ignored.
"""
import ast
import io
import os
import re
import tokenize

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
SCANNED_DIRS = ("worker", "ops")
PROVIDER_ADAPTER_PACKAGES = ()        # repository-relative package directories; none exists until the AI analyst phase

# Hosted-LLM provider SDKs and clients (import names): importing one is calling a provider directly.
PROVIDER_SDK_MODULES = ("google.generativeai", "google.genai", "google.ai.generativelanguage",
                        "google.cloud.aiplatform", "vertexai", "openai", "anthropic", "mistralai", "cohere", "groq",
                        "ollama", "litellm", "langchain", "langchain_core", "langchain_community", "llama_index")
PROVIDER_API_HOST = re.compile(r"generativelanguage\.googleapis\.com|aiplatform\.googleapis\.com|api\.openai\.com|"
                               r"api\.anthropic\.com|api\.mistral\.ai|api\.groq\.com|api\.cohere\.(?:ai|com)", re.I)
CREDENTIAL_NAME = re.compile(
    r"\b[a-z_]*(?:api_?key|access_?token|auth_?token|secret|credential|passw(?:or)?d)[a-z_]*\b", re.I)


def _is_provider_module(name):
    return any(name == m or name.startswith(m + ".") for m in PROVIDER_SDK_MODULES)


def provider_imports(src):
    """The provider SDK modules a source imports: import statements (absolute only; a relative import is the
    project's own) and importlib.import_module / __import__ with a literal name."""
    found = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module] if _is_provider_module(node.module) else \
                [f"{node.module}.{a.name}" for a in node.names]              # e.g. from google import genai
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and \
                isinstance(node.args[0].value, str):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            names = [node.args[0].value] if called in ("import_module", "__import__") else []
        else:
            continue
        found |= {n for n in names if _is_provider_module(n)}
    return found


def code_without_prose(src):
    """The source's tokens without comments and docstrings: names in prose are not code."""
    tree = ast.parse(src)
    docs = set()
    for node in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                          ast.ClassDef))]:
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and \
                isinstance(body[0].value.value, str):
            docs.add((body[0].value.lineno, body[0].value.col_offset))
    return " ".join(tok.string for tok in tokenize.generate_tokens(io.StringIO(src).readline)
                    if tok.type != tokenize.COMMENT and not (tok.type == tokenize.STRING and tok.start in docs))


def in_adapter(rel, adapters=PROVIDER_ADAPTER_PACKAGES):
    return any(rel == a or rel.startswith(a.rstrip("/") + "/") for a in adapters)


def source_violations(rel, src, adapters=PROVIDER_ADAPTER_PACKAGES):
    """Why `src` (repository path `rel`) breaks the provider boundary; [] when it does not."""
    if in_adapter(rel, adapters):
        return []
    out = [f"{rel} imports the provider SDK {m}" for m in sorted(provider_imports(src))]
    host = PROVIDER_API_HOST.search(code_without_prose(src))
    if host:
        out.append(f"{rel} names the provider API host {host.group(0)}")
    return out


def production_files(repo=REPO):
    out = [os.path.join(repo, f) for f in os.listdir(repo) if f.endswith(".py")]
    for d in SCANNED_DIRS:
        for root, dirs, files in os.walk(os.path.join(repo, d)):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            out += [os.path.join(root, f) for f in files if f.endswith(".py")]
    return sorted(out)


def sql_without_comments(sql):
    return re.sub(r"--[^\n]*", " ", re.sub(r"/\*.*?\*/", " ", sql, flags=re.S))


def credential_names(sql):
    return sorted({m.group(0) for m in CREDENTIAL_NAME.finditer(sql_without_comments(sql))})


# ------------------------------------------------------------------------------------------------ the provider boundary

def test_no_production_module_calls_an_ai_provider_outside_the_adapter():
    files = production_files()
    rels = {os.path.relpath(p, REPO).replace(os.sep, "/") for p in files}
    assert "worker/backfill_documents/worker.py" in rels and len(files) > 100       # the scan reached the code
    problems = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            problems += source_violations(os.path.relpath(path, REPO).replace(os.sep, "/"), f.read())
    assert problems == []


def test_the_boundary_check_finds_planted_violations():
    planted = {
        "import google.generativeai as genai\n": "google.generativeai",
        "from google import genai\n": "google.genai",
        "from openai import OpenAI\n": "openai",
        "import importlib\nclient = importlib.import_module('anthropic')\n": "anthropic",
        "URL = 'https://generativelanguage.googleapis.com/v1beta/models'\n": "generativelanguage.googleapis.com",
    }
    for src, what in planted.items():
        found = source_violations("worker/ai_analyst/core.py", src)
        assert len(found) == 1 and what in found[0], (src, found)
    prose = ('"""Reads no Gemini output; google.generativeai is never imported here."""\n'
             '# api.openai.com is not called\nfrom .openai_names import NAMES\n')
    assert source_violations("worker/ai_analyst/core.py", prose) == []
    adapter = "import google.generativeai as genai\n"
    assert source_violations("worker/ai_providers/gemini.py", adapter, adapters=("worker/ai_providers",)) == []
    assert source_violations("worker/ai_providers/gemini.py", adapter) != []        # no adapter package is listed yet


# ------------------------------------------------------------------------------------------------ secrets

def test_no_migration_defines_a_provider_credential():
    mig = os.path.join(REPO, "supabase", "migrations")
    names = sorted(f for f in os.listdir(mig) if f.endswith(".sql"))
    assert len(names) >= 16
    for name in names:
        with open(os.path.join(mig, name), encoding="utf-8") as f:
            assert credential_names(f.read()) == [], name
    assert credential_names("create table ai_runs (id bigint, provider_api_key text, auth_token text)") == \
        ["auth_token", "provider_api_key"]
    assert credential_names("-- a password lives in server configuration\ncreate table t (x int)") == []


# ------------------------------------------------------------------------------------------------ the architecture

def _section(text, number):
    start = re.search(rf"^# {number}\. ", text, re.M)
    end = re.search(rf"^# {number + 1}\. ", text, re.M)
    assert start and end, number
    return " ".join(text[start.start():end.start()].split())


def test_the_master_architecture_keeps_the_provider_rule_and_provenance():
    with open(os.path.join(REPO, "docs", "MASTER_ARCHITECTURE.md"), encoding="utf-8") as f:
        text = f.read()
    route = _section(text, 16)
    for phrase in ("Route 2 --- AI Analyst", "provider-agnostic", "Provider adapter", "Explicit, validated selection",
                   "No silent switching", "Secrets are operational configuration", "Provider capability contract",
                   "An LLM is not automatically eligible", "does not catalogue providers or models"):
        assert phrase in route, phrase
    provenance = _section(text, 17)
    for phrase in ("provider;", "model identifier", "analyst specification (prompt) version",
                   "provider configuration version", "execution timestamps", "input hash", "response hash",
                   "parsed-output hash", "the fallback policy applied", "never retains an API key"):
        assert phrase in provenance, phrase
