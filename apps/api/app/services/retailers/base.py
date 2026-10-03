from pydantic import BaseModel


class RetailerProduct(BaseModel):
    retailer: str
    product_id: str
    name: str
    price: str | None = None
    currency: str | None = None
    url: str
    image_url: str  # best (largest) image we could find
    image_urls: list[str] = []


class Retailer:
    name = "base"

    def enabled(self) -> bool:
        raise NotImplementedError

    async def search(self, query: str, limit: int = 10) -> list[RetailerProduct]:
        raise NotImplementedError
