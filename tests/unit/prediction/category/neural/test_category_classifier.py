import pytest

from robotoff.prediction.category.neural.category_classifier import CategoryClassifier
from robotoff.taxonomy import Taxonomy

MODEL_VERSION = "category-classifier"


class MockResponse:
    def __init__(self, prediction: dict):
        self.prediction = prediction

    def raise_for_status(self):
        pass

    def json(self) -> dict:
        return self.prediction


def _prediction_resp(categories: list[str], confs: list[float]) -> MockResponse:
    return MockResponse(
        prediction={
            "predictions": [
                {"output_mapper_layer": confs, "output_mapper_layer_1": categories},
            ]
        }
    )


def test_predict_missing_data():
    classifier = CategoryClassifier(None)

    predicted, _ = classifier.predict(
        {"WRONG_ingredients_tags": ["ingredient1"]},
    )

    assert not predicted


@pytest.mark.parametrize(
    "data",
    [
        # missing ingredients_tags
        {"product_name": "Test Product"},
        # ingredients_tag empty
        {
            "ingredients_tags": [],
            "product_name": "Test Product",
        },
    ],
    ids=[
        "missing ingredients_tags",
        "ingredients_tag empty",
    ],
)
def test_predict_ingredients_only(mocker, data):
    mocker.patch(
        "robotoff.prediction.category.neural.category_classifier.keras_category_classifier_2_0.http_session.post",
        return_value=_prediction_resp(["en:meat"], [0.99]),
    )
    classifier = CategoryClassifier({"en:meat": {"names": "meat"}})
    predictions, debug = classifier.predict(data)
    assert debug == {
        "inputs": {
            "ingredient": data["ingredients_tags"],
            "product_name": [data["product_name"]],
        },
        "model_name": "keras-2.0",
        "threshold": 0.5,
    }
    assert len(predictions) == 1
    prediction = predictions[0]
    assert prediction.value_tag == "en:meat"
    assert prediction.confidence == 0.99


@pytest.mark.parametrize(
    "data",
    [
        {"ingredients_tags": ["ingredient1"]},  # missing product_name
        {"ingredients_tags": ["ingredient1"], "product_name": ""},  # product_name empty
    ],
    ids=[
        "missing product_name",
        "product_name empty",
    ],
)
def test_predict_product_no_title(mocker, data):
    mocker.patch(
        "robotoff.prediction.category.neural.category_classifier.keras_category_classifier_2_0.http_session.post",
        return_value=_prediction_resp(["en:meat"], [0.99]),
    )
    classifier = CategoryClassifier({"en:meat": {"names": "meat"}})
    predictions, _ = classifier.predict(data)
    assert len(predictions) == 0


@pytest.mark.parametrize(
    "deepest_only,mock_response,expected_values",
    [
        # Nothing predicted - nothing returned.
        (False, _prediction_resp([], []), []),
        # Low prediction confidences - nothing returned.
        (False, _prediction_resp(["en:meat", "en:fish"], [0.3, 0.3]), []),
        # Only the high confidence prediction is returned.
        (
            False,
            _prediction_resp(["en:fish", "en:meat"], [0.7, 0.3]),
            [("en:fish", 0.7)],
        ),
        # Only the leaves of the taxonomy are returned.
        (
            True,
            _prediction_resp(["en:fish", "en:smoked-salmon"], [0.8, 0.8]),
            [("en:smoked-salmon", 0.8)],
        ),
    ],
)
def test_predict(mocker, deepest_only, mock_response, expected_values):
    category_taxonomy = Taxonomy.from_dict(
        {
            "en:meat": {
                "names": "meat",
            },
            "en:fish": {
                "names": "fish",
            },
            "en:salmon": {
                "names": "salmon",
                "parents": ["en:fish"],
            },
            "en:smoked-salmon": {
                "names": "salmon",
                "parents": ["en:salmon"],
            },
        }
    )

    classifier = CategoryClassifier(category_taxonomy)

    mocker.patch(
        "robotoff.prediction.category.neural.category_classifier.keras_category_classifier_2_0.http_session.post",
        return_value=mock_response,
    )

    predictions, _ = classifier.predict(
        {"ingredients_tags": ["ingredient1"], "product_name": "Test Product"},
        deepest_only,
    )

    assert len(predictions) == len(expected_values)

    for prediction, (value_tag, confidence) in zip(predictions, expected_values):
        assert prediction.value_tag == value_tag
        assert prediction.confidence == confidence
