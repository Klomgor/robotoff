from typing import Optional

import numpy as np
from tritonclient.grpc import service_pb2

from robotoff.off import generate_json_ocr_url
from robotoff.prediction.ocr.core import get_ocr_result
from robotoff.triton import (
    deserialize_byte_tensor,
    get_triton_inference_stub,
    serialize_byte_tensor,
)
from robotoff.types import JSONType, NeuralCategoryClassifierModel
from robotoff.utils import http_session

from .preprocessing import NUTRIMENT_NAMES, generate_inputs_from_product


def fetch_ocr_texts(product: JSONType) -> list[str]:
    barcode = product.get("code")
    if not barcode:
        return []

    ocr_texts = []
    for image_id in (id_ for id_ in product.get("images", {}).keys() if id_.isdigit()):
        ocr_url = generate_json_ocr_url(barcode, image_id)
        ocr_result = get_ocr_result(ocr_url, http_session, error_raise=False)
        if ocr_result:
            ocr_texts.append(ocr_result.get_full_text())

    return ocr_texts


def predict(
    product: JSONType,
    ocr_texts: list[str],
    model_name: NeuralCategoryClassifierModel,
    threshold: Optional[float] = None,
) -> tuple[list[tuple[str, float]], JSONType]:
    if threshold is None:
        threshold = 0.5

    inputs = generate_inputs_from_product(product, ocr_texts)
    debug: JSONType = {
        "model_name": model_name.value,
        "threshold": threshold,
        "inputs": inputs,
    }
    scores, labels = _predict(inputs, model_name)
    indices = np.argsort(-scores)

    category_predictions: list[tuple[str, float]] = []

    for idx in indices:
        confidence = float(scores[idx])
        category = labels[idx]
        # We only consider predictions with a confidence score of `threshold` and above.
        if confidence >= threshold:
            category_predictions.append((category, confidence))
        else:
            break

    return category_predictions, debug


model_input_flags: dict[NeuralCategoryClassifierModel, dict] = {
    NeuralCategoryClassifierModel.keras_sota_3_0: {},
    NeuralCategoryClassifierModel.keras_ingredient_ocr_3_0: {},
    NeuralCategoryClassifierModel.keras_baseline_3_0: {
        "add_ingredients_ocr_tags": False
    },
    NeuralCategoryClassifierModel.keras_original_3_0: {
        "add_ingredients_ocr_tags": False,
        "add_nutriments": False,
    },
    NeuralCategoryClassifierModel.keras_product_name_only_3_0: {
        "add_ingredients_ocr_tags": False,
        "add_nutriments": False,
        "add_ingredient_tags": False,
    },
}

triton_model_names = {
    NeuralCategoryClassifierModel.keras_sota_3_0: "category-classifier-keras-sota-3.0",
    NeuralCategoryClassifierModel.keras_ingredient_ocr_3_0: "category-classifier-keras-ingredient-ocr-3.0",
    NeuralCategoryClassifierModel.keras_baseline_3_0: "category-classifier-keras-baseline-3.0",
    NeuralCategoryClassifierModel.keras_original_3_0: "category-classifier-keras-original-3.0",
    NeuralCategoryClassifierModel.keras_product_name_only_3_0: "category-classifier-keras-product-name-only-3.0",
}


def _predict(
    inputs: JSONType, model_name: NeuralCategoryClassifierModel
) -> tuple[np.ndarray, list[str]]:
    request = build_triton_request(
        inputs,
        model_name=triton_model_names[model_name],
        **model_input_flags[model_name],
    )
    stub = get_triton_inference_stub()
    response = stub.ModelInfer(request)
    scores = np.frombuffer(response.raw_output_contents[0], dtype=np.float32,).reshape(
        (1, -1)
    )[0]
    labels = deserialize_byte_tensor(response.raw_output_contents[1])
    return scores, labels


def build_triton_request(
    inputs: JSONType,
    model_name: str,
    add_product_name: bool = True,
    add_ingredient_tags: bool = True,
    add_nutriments: bool = True,
    add_ingredients_ocr_tags: bool = True,
):
    product_name = inputs["product_name"]
    ingredients_tags = inputs["ingredients_tags"]
    ingredients_ocr_tags = inputs["ingredients_ocr_tags"]
    request = service_pb2.ModelInferRequest()
    request.model_name = model_name

    if add_product_name:
        product_name_input = service_pb2.ModelInferRequest().InferInputTensor()
        product_name_input.name = "product_name"
        product_name_input.datatype = "BYTES"
        product_name_input.shape.extend([1, 1])
        request.inputs.extend([product_name_input])

    if add_ingredient_tags:
        ingredients_tags_input = service_pb2.ModelInferRequest().InferInputTensor()
        ingredients_tags_input.name = "ingredients_tags"
        ingredients_tags_input.datatype = "BYTES"
        ingredients_tags_input.shape.extend([1, len(ingredients_tags)])
        request.inputs.extend([ingredients_tags_input])

    if add_nutriments:
        for nutriment_name in NUTRIMENT_NAMES:
            nutriment_input = service_pb2.ModelInferRequest().InferInputTensor()
            nutriment_input.name = nutriment_name
            nutriment_input.datatype = "FP32"
            nutriment_input.shape.extend([1, 1])
            request.inputs.extend([nutriment_input])

    if add_ingredients_ocr_tags:
        ingredients_ocr_tags_input = service_pb2.ModelInferRequest().InferInputTensor()
        ingredients_ocr_tags_input.name = "ingredients_ocr_tags"
        ingredients_ocr_tags_input.datatype = "BYTES"
        ingredients_ocr_tags_input.shape.extend([1, len(ingredients_ocr_tags)])
        request.inputs.extend([ingredients_ocr_tags_input])

    if add_product_name:
        request.raw_input_contents.extend(
            [serialize_byte_tensor(np.array([[product_name]], dtype=object))]
        )

    if add_ingredient_tags:
        request.raw_input_contents.extend(
            [serialize_byte_tensor(np.array([ingredients_tags], dtype=object))]
        )

    if add_nutriments:
        for nutriment_name in NUTRIMENT_NAMES:
            value = inputs[nutriment_name]
            request.raw_input_contents.extend(
                [np.array([[value]], dtype=np.float32).tobytes()]
            )

    if add_ingredients_ocr_tags:
        request.raw_input_contents.extend(
            [serialize_byte_tensor(np.array([ingredients_ocr_tags], dtype=object))]
        )

    return request