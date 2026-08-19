from __future__ import annotations

from typing import Any

from ticker_analyzer.provider_http import JsonApiClient


class NbpClient(JsonApiClient):
    def exchange_rate(self, currency: str, *, table: str = "A", top_count: int = 1) -> dict[str, Any]:
        return self.get_json(
            f"https://api.nbp.pl/api/exchangerates/rates/{table}/{currency.lower()}/last/{top_count}/",
            headers={"Accept": "application/json"},
        )
    def exchange_rates(self, currency: str, start: str, end: str, *, table: str = "A") -> list[dict[str, Any]]:
        payload = self.get_json(
            f"https://api.nbp.pl/api/exchangerates/rates/{table}/{currency.lower()}/{start}/{end}/",
            params={"format": "json"},
            headers={"Accept": "application/json"},
        )
        return list(payload.get("rates", []))


class FdicClient(JsonApiClient):
    def institutions(self, *, cert: str | int, limit: int = 100) -> dict[str, Any]:
        return self.get_json(
            "https://banks.data.fdic.gov/api/institutions",
            params={"filters": f"CERT:{cert}", "format": "json", "limit": limit},
        )

    def financials(
        self,
        *,
        cert: str | int,
        fields: str = "CERT,REPDTE,ASSET,DEP,NETINC,ROA",
        limit: int = 8,
    ) -> dict[str, Any]:
        return self.get_json(
            "https://banks.data.fdic.gov/api/financials",
            params={
                "filters": f"CERT:{cert}",
                "fields": fields,
                "sort_by": "REPDTE",
                "sort_order": "DESC",
                "format": "json",
                "limit": limit,
            },
        )


class GleifClient(JsonApiClient):
    def lei_records(self, *, legal_name: str, page_size: int = 10) -> dict[str, Any]:
        return self.get_json(
            "https://api.gleif.org/api/v1/lei-records",
            params={"filter[entity.legalName]": legal_name, "page[size]": page_size},
            headers={"Accept": "application/vnd.api+json"},
        )


class FinraClient(JsonApiClient):
    def __init__(self, *, sandbox: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.sandbox = sandbox

    def broker_dealers(self, *, crd_number: str | int) -> Any:
        dataset = "brokerDealerFirmListMock" if self.sandbox else "brokerDealerFirmList"
        return self.get_json(
            f"https://api.finra.org/data/group/registration/name/{dataset}",
            params={"firmCrdNumber": crd_number},
            headers={"Accept": "application/json"},
        )
