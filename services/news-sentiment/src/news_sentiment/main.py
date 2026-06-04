from loguru import logger
from quixstreams import Application

from news_sentiment.sentiment_extractor import SentimentExtractor


def run(
    # kafka parameters
    kafka_broker_address: str,
    kafka_input_topic: str,
    kafka_output_topic: str,
    kafka_consumer_group: str,
    sentiment_extractor: SentimentExtractor,
):
    """
    Ingests news articles from Kafka and output structured output with sentiment scores.

    """

    app = Application(
        broker_address=kafka_broker_address,
        consumer_group=kafka_consumer_group,
        auto_offset_reset='earliest',
    )

    # Define the Kafka topics
    news_topic = app.topic(kafka_input_topic, value_deserializer='json')
    news_sentiment_topic = app.topic(kafka_output_topic, value_serializer='json')

    # Step 1.Create a Streaming DataFrame connected to the input Kafka topic
    sdf = app.dataframe(topic=news_topic)

    def get_sentiment_scores(news_item: dict) -> list[dict]:
        timestamp_ms = news_item['timestamp_ms']
        news: str = news_item['title'] + ' ' + (news_item.get('description') or '')

        # return [
        #     {'coin': 'BTC', 'score': 1, 'timestamp_ms': timestamp_ms},
        #     {'coin': 'ETH', 'score': -1, 'timestamp_ms': timestamp_ms}
        #         ]
        output = sentiment_extractor.extract_sentiment_scores(news)

        sentiment_scores = [
            {'coin': score.coin, 'score': score.score, 'timestamp_ms': timestamp_ms}
            for score in output.scores
        ]

        return sentiment_scores

    sdf = sdf.apply(get_sentiment_scores, expand=True)

    # logging on the console
    # sdf = sdf.update(lambda value: logger.debug(f'Updated candle: {value}'))
    # sdf = sdf.update(lambda _:breakpoint())

    # Apply a custom function and inform StreamingDataFrame
    # to provide a State instance to it using "stateful=True"
    # logging on the console
    sdf = sdf.update(lambda value: logger.debug(f'Final message: {value}'))

    # Produce alerts to the output topic
    sdf = sdf.to_topic(news_sentiment_topic)

    # Run the streaming application (app automatically tracks the sdf!)
    # app.clear_state()
    app.run()


if __name__ == '__main__':
    from news_sentiment.config import config

    sentiment_extractor = SentimentExtractor(model='GPT4mini')

    run(
        kafka_broker_address=config.kafka_broker_address,
        kafka_input_topic=config.kafka_input_topic,
        kafka_output_topic=config.kafka_output_topic,
        kafka_consumer_group=config.kafka_consumer_group,
        sentiment_extractor=sentiment_extractor,
    )
