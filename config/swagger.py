import json
from pathlib import Path

from django.http import HttpResponse, JsonResponse


def openapi_schema(request):
    """Serve the OpenAPI JSON spec."""
    spec_path = Path(__file__).parent / "openapi.json"
    spec = json.loads(spec_path.read_text())
    return JsonResponse(spec, safe=False)


def swagger_ui(request):
    """Serve Swagger UI HTML page."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ziona API Docs</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
    <style>
        body { margin: 0; background: #fafafa; }
        .topbar { display: none !important; }
    </style>
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
        SwaggerUIBundle({
            url: '/api/schema/',
            dom_id: '#swagger-ui',
            deepLinking: true,
            presets: [
                SwaggerUIBundle.presets.apis,
                SwaggerUIBundle.SwaggerUIStandalonePreset
            ],
            layout: 'BaseLayout',
            defaultModelsExpandDepth: 1,
            docExpansion: 'list',
            filter: true,
            tryItOutEnabled: true,
        });
    </script>
</body>
</html>"""
    return HttpResponse(html, content_type="text/html")
