import os
import re
import tempfile

import numpy as np
import pandas as pd
import pytest
from ray.air.util.data_batch_conversion import _convert_pandas_to_batch_type
from ray.train.predictor import TYPE_TO_ENUM
from sklearn.ensemble import RandomForestClassifier

import ray.cloudpickle as cpickle
from ray.train import Checkpoint
from ray.air.constants import MAX_REPR_LENGTH, MODEL_KEY
from ray.data.preprocessor import Preprocessor
from ray.train.sklearn import SklearnCheckpoint, SklearnPredictor
from typing import Tuple

from ray.train.tests.dummy_preprocessor import DummyPreprocessor


dummy_data = np.array([[1, 2], [3, 4], [5, 6]])
dummy_target = np.array([0, 1, 0])
model = RandomForestClassifier(n_estimators=10, random_state=0).fit(
    dummy_data, dummy_target
)


def test_repr():
    predictor = SklearnPredictor(estimator=model)

    representation = repr(predictor)

    assert len(representation) < MAX_REPR_LENGTH
    pattern = re.compile("^SklearnPredictor\\((.*)\\)$")
    assert pattern.match(representation)


def create_checkpoint_preprocessor() -> Tuple[Checkpoint, Preprocessor]:
    preprocessor = DummyPreprocessor()

    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint = SklearnCheckpoint.from_estimator(
            estimator=model, path=tmpdir, preprocessor=preprocessor
        )
        # Serialize to dict so we can remove the temporary directory
        checkpoint = SklearnCheckpoint.from_dict(checkpoint.to_dict())

    return checkpoint, preprocessor


def test_sklearn_checkpoint():
    checkpoint, preprocessor = create_checkpoint_preprocessor()

    predictor = SklearnPredictor(estimator=model, preprocessor=preprocessor)
    checkpoint_predictor = SklearnPredictor.from_checkpoint(checkpoint)

    assert np.allclose(
        checkpoint_predictor.estimator.feature_importances_,
        predictor.estimator.feature_importances_,
    )
    assert checkpoint_predictor.get_preprocessor() == predictor.get_preprocessor()


@pytest.mark.parametrize("batch_type", [np.ndarray, pd.DataFrame, dict])
def test_predict(batch_type):
    preprocessor = DummyPreprocessor()
    predictor = SklearnPredictor(estimator=model, preprocessor=preprocessor)

    raw_batch = pd.DataFrame([[1, 2], [3, 4], [5, 6]])
    data_batch = _convert_pandas_to_batch_type(raw_batch, type=TYPE_TO_ENUM[batch_type])
    predictions = predictor.predict(data_batch)

    assert len(predictions) == 3
    assert predictor.get_preprocessor().has_preprocessed


def test_predict_set_cpus(ray_start_4_cpus):
    preprocessor = DummyPreprocessor()
    predictor = SklearnPredictor(estimator=model, preprocessor=preprocessor)

    data_batch = np.array([[1, 2], [3, 4], [5, 6]])
    predictions = predictor.predict(data_batch, num_estimator_cpus=2)

    assert len(predictions) == 3
    assert predictor.get_preprocessor().has_preprocessed
    assert predictor.estimator.n_jobs == 2


def test_predict_feature_columns():
    preprocessor = DummyPreprocessor()
    predictor = SklearnPredictor(estimator=model, preprocessor=preprocessor)

    data_batch = np.array([[1, 2, 7], [3, 4, 8], [5, 6, 9]])
    predictions = predictor.predict(data_batch, feature_columns=[0, 1])

    assert len(predictions) == 3
    assert predictor.get_preprocessor().has_preprocessed


def test_predict_feature_columns_pandas():
    pandas_data = pd.DataFrame(dummy_data, columns=["A", "B"])
    pandas_target = pd.Series(dummy_target)
    pandas_model = RandomForestClassifier(n_estimators=10, random_state=0).fit(
        pandas_data, pandas_target
    )
    preprocessor = DummyPreprocessor()
    predictor = SklearnPredictor(estimator=pandas_model, preprocessor=preprocessor)
    data_batch = pd.DataFrame(
        np.array([[1, 2, 7], [3, 4, 8], [5, 6, 9]]), columns=["A", "B", "C"]
    )
    predictions = predictor.predict(data_batch, feature_columns=["A", "B"])

    assert len(predictions) == 3
    assert predictor.get_preprocessor().has_preprocessed


def test_predict_no_preprocessor():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, MODEL_KEY), "wb") as f:
            cpickle.dump(model, f)

        checkpoint = Checkpoint.from_directory(tmpdir)
        predictor = SklearnPredictor.from_checkpoint(checkpoint)

    data_batch = np.array([[1, 2], [3, 4], [5, 6]])
    predictions = predictor.predict(data_batch)

    assert len(predictions) == 3


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main(["-v", "-x", __file__]))
