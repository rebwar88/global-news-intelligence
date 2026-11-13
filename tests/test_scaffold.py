def test_project_scaffold_is_importable():
    import platform

    assert platform.__name__ == "platform"
