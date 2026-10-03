from pydantic import BaseModel


class RetailerProduct(BaseModel):
    retailer: str
    product_id: str
    name: str
    price: str | None = None
    currency: str | None = None
    url: str  # product page
    affiliate_url: str | None = None  # tracked link, when the retailer API provides one
    image_url: str  # best (largest) image we could find
    image_urls: list[str] = []


class Retailer:
    name = "base"

    def enabled(self) -> bool:
        raise NotImplementedError

    async def search(self, query: str, limit: int = 10, max_price: float | None = None) -> list[RetailerProduct]:
        raise NotImplementedError
