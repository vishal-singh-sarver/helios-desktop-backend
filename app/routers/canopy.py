# Canopy endpoints are handled in plantarch.py (same plugin)
# This file re-exports the router so main.py can include it separately if needed.
from app.routers.plantarch import router
