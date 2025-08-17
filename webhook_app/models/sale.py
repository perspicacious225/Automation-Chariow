from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class Sale:
    id: str
    status: str
    product_name: str
    product_id: str
    customer_name: str
    customer_phone: str
    customer_country: str
    customer_email: str
    store_name: str
    store_url: str
    checkout_url: str
    amount: str
    created_at: str
    product_value: str
    current_year:str

    @classmethod
    def from_webhook(cls, payload: Dict[str, Any]) -> 'Sale':
        sale = payload.get("sale", {})
        return cls(
            id=sale.get("id"),
            status=sale.get("status"),
            product_name=payload.get("product", {}).get("name"),
            product_id=payload.get("product", {}).get("id"),
            customer_name=payload.get("customer", {}).get("name"),
            customer_phone=payload.get("customer", {}).get("phone"),
            customer_country=payload.get("customer", {}).get("country"),
            customer_email=payload.get("customer", {}).get("email"),
            store_name=payload.get("store", {}).get("name"),
            store_url=payload.get("store", {}).get("url"),
            checkout_url=payload.get("checkout", {}).get("url"),
            amount=sale.get("amount", {}).get("value"),
            created_at=sale.get("created_at", "").split("T")[0],
            product_value=payload.get("product", {}).get("price", {}).get("value"),
            current_year=sale.get("created_at", "").split("T")[0].split("-")[0]
        )
    
