from loguru import logger
from quixstreams import Application

from news.news_data_source import NewsDataSource
from news.news_downloader import NewsDownloader


def main(
    kafka_broker_address: str,
    kafka_output_topic: str,
    news_source: NewsDataSource,
):
    """
    Gets news from Cryptopanic and pushes it to a Kafka topic.

    Args:
        kafka_broker_address: The address of the Kafka broker.
        kafka_topic: The topic to push the news to.
        news_source: The news source to get the news from.
    Returns:
        None
    """
    logger.info('Hello from news!')

    app = Application(broker_address=kafka_broker_address)

    # Topic where we will push the news to
    output_topic = app.topic(name=kafka_output_topic, value_serializer='json')

    # Create the streaming dataframe
    sdf = app.dataframe(source=news_source)

    # Let's print to check this thing is working
    # sdf.print(metadata=True)

    # Send the final messages to the output topic
    sdf = sdf.to_topic(output_topic)

    app.run()


if __name__ == '__main__':
    from news.config import config

    # Create the news source either for
    # - live data -> via polling the CryptoPanic API
    # - historical data -> via reading a CSV file that we got from an external URL

    news_source = NewsDataSource(
        news_downloader=NewsDownloader(
            cryptopanic_api_key=config.cryptopanic_api_key,
        ),
        polling_interval_sec=config.polling_interval_sec,
    )

    # Run the streaming application
    main(
        news_source=news_source,
        kafka_broker_address=config.kafka_broker_address,
        kafka_output_topic=config.kafka_output_topic,
    )
