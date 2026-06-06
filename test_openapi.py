#!/usr/bin/env python
import sys
from app.main import app

try:
    from fastapi.openapi.utils import get_openapi
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    print('Schema generated successfully')
except Exception as e:
    print(f'Error generating schema: {type(e).__name__}: {e}')
    import traceback
    traceback.print_exc()
