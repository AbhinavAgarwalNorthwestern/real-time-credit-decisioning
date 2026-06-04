import json

from loguru import logger
from opik import Opik


def mark_item_as_correct(dataset, item):
    dataset.delete(items_ids=[item['id']])
    new_item = {**item, 'is_human_verified': True}
    dataset.insert([new_item])


def ask_human_for_correction(dataset, item):
    while True:
        expected_output = input('Please provide the expected output (a list of JSON): ')

        try:
            expected_output = json.loads(expected_output)
            break

        except json.JSONDecodeError:
            print('Invalid JSON, please try again')
            continue

    while True:
        expected_reason = input('Please provide the expected reason: ')

        if expected_reason:
            break

        else:
            print('Invalid reason, please try again')
            continue

    item['expected_output'] = expected_output
    item['expected_reason'] = expected_reason

    mark_item_as_correct(dataset, item)


def curate_dataset(dataset_name: str):
    """
    Curates the dataset with a human in the loop

    """

    logger.info(f'Curating dataset: {dataset_name}')
    client = Opik()
    dataset = client.get_or_create_dataset(name=dataset_name)

    dataset_items = json.loads(dataset.to_json())
    logger.info(f'Loaded {len(dataset_items)} items from dataset')

    dataset_items = [
        item for item in dataset_items if not item.get('is_human_verified', False)
    ]
    for item in dataset_items:
        print('input: ', item['input'])
        print('----------------------------------')
        print('expected_output: ', item['expected_output'])
        print('----------------------------------')
        print('reason: ', item['expected_reason'])
        print('----------------------------------')
        print('teacher_model: ', item['teacher_model'])
        print('----------------------------------')

        while True:
            is_correct = input('Is the item correct? (y/n)?: ')

            if is_correct == 'y':
                print('Item is correct')
                mark_item_as_correct(dataset, item)
                break

            elif is_correct == 'n':
                print('Item is incorrect')
                ask_human_for_correction(dataset, item)
                break

            else:
                print('Invalid input')
                continue


if __name__ == '__main__':
    from fire import Fire

    Fire(curate_dataset)
