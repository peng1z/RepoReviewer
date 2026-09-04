def test_main_exposes_the_asgi_app() -> None:
    import repo_reviewer.main as main
    assert hasattr(main, "app")
