"""Route modules for mock SaaS — orders, customers, inventory."""

from mock_saas.routes.customers import router as customers_router
from mock_saas.routes.inventory import router as inventory_router
from mock_saas.routes.orders import router as orders_router

__all__ = ["customers_router", "inventory_router", "orders_router"]
