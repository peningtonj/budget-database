class NoStoreApiMiddleware:
    """Every /api/ response gets Cache-Control: no-store.

    DRF sets no caching headers of its own, so with nothing here a
    response's cacheability is entirely up to whatever heuristic the
    browser -- or, more to the point, a corporate SSL-inspecting proxy
    sitting between a user and Render -- decides to apply to a bare
    JSON response with no explicit directive. Confirmed suspicious in
    practice: a user reported one specific search-topic/ query
    ("child care") failing instantly on their work network while other
    queries succeeded normally -- consistent with a proxy having cached
    a genuine 500 this same query hit before an earlier bug fix (each
    query string is its own cache key, so only that exact term would be
    affected) and now serving that stale failure straight back without
    ever reaching the now-fixed server again. An explicit no-store
    doesn't retroactively evict anything already cached, but stops it
    from recurring for every dynamic API response going forward -- this
    data changes with every request (a search result, a $ figure) and
    should never be cached by an intermediate hop.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response["Cache-Control"] = "no-store"
        return response
