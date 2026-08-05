from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_is_local_only_healthy_and_device_adaptive():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:3200:3200"' in compose
    assert '"127.0.0.1:8200:8200"' in compose
    assert compose.count("healthcheck:") == 2
    assert 'PHYLO_PUBLIC_IQTREE_THREADS: "${PHYLO_PUBLIC_IQTREE_THREADS:-AUTO}"' in compose
    assert '"3200:3200"' not in compose.replace('"127.0.0.1:3200:3200"', "")
    assert '"8200:8200"' not in compose.replace('"127.0.0.1:8200:8200"', "")


def test_public_runtime_configuration_has_no_author_machine_path():
    paths = [
        ROOT / ".env.example",
        ROOT / "Dockerfile.api",
        ROOT / "compose.yaml",
        ROOT / "backend" / "app.py",
        *sorted((ROOT / "scripts").glob("*")),
    ]
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in paths
        if path.is_file()
    )

    assert "/home/" not in combined
    assert "PHYLO_PUBLIC_IQTREE_THREADS=AUTO" in combined


def test_bilingual_publication_documentation_is_complete():
    english = {
        "README.md",
        "LOCAL_INSTALLATION.md",
        "METHODS.md",
        "INPUT_OUTPUT.md",
        "VALIDATION.md",
        "PRIVACY_SECURITY.md",
        "DEPLOYMENT.md",
        "TROUBLESHOOTING.md",
        "CITATION.md",
    }
    chinese = {path.name for path in (ROOT / "docs" / "zh-CN").glob("*.md")}
    root_english = {path.name for path in (ROOT / "docs").glob("*.md")}

    assert english <= root_english
    assert english <= chinese
    assert (ROOT / "README.md").is_file()
    assert (ROOT / "README.zh-CN.md").is_file()
