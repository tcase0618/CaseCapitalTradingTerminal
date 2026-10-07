from routers.research import router


def test_research_routes_remain_read_only_and_registered():
    routes = {route.path: route.methods for route in router.routes}

    assert "/research/accountant/status" in routes
    assert "/research/accountant/{ticker}" in routes
    assert "/data/finance-toolkit/research/{ticker}" in routes
    assert all(methods == {"GET"} for methods in routes.values())
