#!/usr/bin/env python3
import pkgutil
__path__ = pkgutil.extend_path(__path__, __name__)

import connexion
from connexion.resolver import RestyResolver

# Extracted app for global visibility, allow external/production server access
app = connexion.FlaskApp(__name__)
app.add_api(
    "fair-api.yaml",
    arguments={"title": "FAIR evaluator"},
    resolver=RestyResolver("fair_eva.api"),
)

application = app.app

def main():
    app.run(port=9090) # Hardcoded port, to be changed, although only used in direct execution

if __name__ == "__main__":
    main()
