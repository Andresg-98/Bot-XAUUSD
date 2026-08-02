from __future__ import annotations

import pytest

from bot_xauusd.ingestion.news_sentiment import AlphaVantageNewsSentimentClient, NewsSentimentApiError


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.requested_params: list[dict] = []

    def get(self, url: str, params: dict, timeout: float) -> FakeResponse:
        self.requested_params.append(params)
        return self._response


def test_get_latest_events_averages_sentiment_scores() -> None:
    payload = {
        "feed": [
            {"title": "A", "overall_sentiment_score": 0.4},
            {"title": "B", "overall_sentiment_score": -0.2},
        ]
    }
    session = FakeSession(FakeResponse(payload))
    client = AlphaVantageNewsSentimentClient(api_key="fake-key", session=session)

    eventos = client.get_latest_events()

    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.valor_real == pytest.approx(0.1)
    assert evento.fuente == "Alpha Vantage"
    assert evento.valor_esperado is None
    assert evento.valor_previo is None


def test_get_latest_events_returns_empty_list_without_articles() -> None:
    session = FakeSession(FakeResponse({"feed": []}))
    client = AlphaVantageNewsSentimentClient(api_key="fake-key", session=session)

    assert client.get_latest_events() == []


def test_missing_api_key_raises() -> None:
    with pytest.raises(ValueError):
        AlphaVantageNewsSentimentClient(api_key="")


def test_rate_limit_information_field_raises_even_with_200_status() -> None:
    session = FakeSession(FakeResponse({"Information": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."}))
    client = AlphaVantageNewsSentimentClient(api_key="fake-key", session=session)

    with pytest.raises(NewsSentimentApiError):
        client.get_latest_events()


def test_error_message_field_raises() -> None:
    session = FakeSession(FakeResponse({"Error Message": "the parameter apikey is invalid"}))
    client = AlphaVantageNewsSentimentClient(api_key="fake-key", session=session)

    with pytest.raises(NewsSentimentApiError):
        client.get_latest_events()


def test_non_200_response_raises() -> None:
    session = FakeSession(FakeResponse({"error": "boom"}, status_code=500))
    client = AlphaVantageNewsSentimentClient(api_key="fake-key", session=session)

    with pytest.raises(NewsSentimentApiError):
        client.get_latest_events()


def test_requests_configured_topics() -> None:
    session = FakeSession(FakeResponse({"feed": []}))
    client = AlphaVantageNewsSentimentClient(api_key="fake-key", session=session, topics="financial_markets")

    client.get_latest_events(limit=10)

    assert session.requested_params[0]["topics"] == "financial_markets"
    assert session.requested_params[0]["limit"] == 10
