def test_project_package_is_importable():
    import gni

    assert gni.__doc__ == "Global News Intelligence platform package."
