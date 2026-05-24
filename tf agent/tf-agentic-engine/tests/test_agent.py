def test_app_exists():
    import src.agent as agent

    assert hasattr(agent, "app")
    assert hasattr(agent.app, "invoke")
