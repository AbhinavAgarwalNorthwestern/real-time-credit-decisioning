from typing import Any

from opik.evaluation.metrics import base_metric, score_result

from news_sentiment.baml_client.types import SentimentScore


class SameScoreMetric(base_metric.BaseMetric):
    def __init__(self, name: str, coin: str):
        # super().__init__(name)
        self.name = name
        self.coin = coin

    def _has_non_zero_score(self, scores: list[dict[str, int]]) -> bool:
        """
        Returns True if 'scores' list has a non-zero element for the coin 'self.coin' , false otherwise


        """
        return any(x['coin'] == self.coin and x['score'] != 0 for x in scores)

    def _get_score_for_coin(self, scores: list[dict[str, int]]) -> int:
        return [x for x in scores if x['coin'] == self.coin][0]['score']

    def score(
        self,
        input: str,
        scores: list[SentimentScore],
        expected_output: list[dict[str, int]],
        **ignored_kwargs: Any,
    ):
        # transform 'scores' as a list of dictionaries

        scores = [{'coin': str(x.coin), 'score': x.score} for x in scores]

        value = 0

        if (not self._has_non_zero_score(scores)) and (
            not self._has_non_zero_score(expected_output)
        ):
            value = 1

        elif self._has_non_zero_score(scores) and self._has_non_zero_score(
            expected_output
        ):
            value = (
                1
                if self._get_score_for_coin(scores)
                == self._get_score_for_coin(expected_output)
                else 0
            )

        return score_result.ScoreResult(
            value=value,
            name=self.name,
            # reason="Optional reason for the score"
        )
