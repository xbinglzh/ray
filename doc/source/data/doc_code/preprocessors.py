# flake8: noqa
# isort: skip_file

# __preprocessor_setup_start__
import pandas as pd
import ray
from ray.data.preprocessors import MinMaxScaler
from ray.data.preprocessors.scaler import StandardScaler

# Generate two simple datasets.
dataset = ray.data.range(8)
dataset1, dataset2 = dataset.split(2)

print(dataset1.take())
# [{'id': 0}, {'id': 1}, {'id': 2}, {'id': 3}]

print(dataset2.take())
# [{'id': 4}, {'id': 5}, {'id': 6}, {'id': 7}]
# __preprocessor_setup_end__


# __preprocessor_fit_transform_start__
# Fit the preprocessor on dataset1, and transform both dataset1 and dataset2.
preprocessor = MinMaxScaler(["id"])

dataset1_transformed = preprocessor.fit_transform(dataset1)
print(dataset1_transformed.take())
# [{'id': 0.0}, {'id': 0.3333333333333333}, {'id': 0.6666666666666666}, {'id': 1.0}]

dataset2_transformed = preprocessor.transform(dataset2)
print(dataset2_transformed.take())
# [{'id': 1.3333333333333333}, {'id': 1.6666666666666667}, {'id': 2.0}, {'id': 2.3333333333333335}]
# __preprocessor_fit_transform_end__


# __preprocessor_transform_batch_start__
batch = pd.DataFrame({"id": list(range(8, 12))})
batch_transformed = preprocessor.transform_batch(batch)
print(batch_transformed)
#          id
# 0  2.666667
# 1  3.000000
# 2  3.333333
# 3  3.666667
# __preprocessor_transform_batch_end__


# __trainer_start__
import ray

from ray.data.preprocessors import MinMaxScaler
from ray.train.xgboost import XGBoostTrainer
from ray.train import ScalingConfig

train_dataset = ray.data.from_items([{"x": x, "y": 2 * x} for x in range(0, 32, 3)])
valid_dataset = ray.data.from_items([{"x": x, "y": 2 * x} for x in range(1, 32, 3)])

preprocessor = MinMaxScaler(["x"])

trainer = XGBoostTrainer(
    label_column="y",
    params={"objective": "reg:squarederror"},
    scaling_config=ScalingConfig(num_workers=2),
    datasets={"train": train_dataset, "valid": valid_dataset},
    preprocessor=preprocessor,
)
result = trainer.fit()
# __trainer_end__


# __checkpoint_start__
import os
import ray.cloudpickle as cpickle
from ray.air.constants import PREPROCESSOR_KEY

checkpoint = result.checkpoint
with checkpoint.as_directory() as checkpoint_path:
    path = os.path.join(checkpoint_path, PREPROCESSOR_KEY)
    with open(path, "rb") as f:
        preprocessor = cpickle.load(f)
    print(preprocessor)
# MixMaxScaler(columns=['x'], stats={'min(x)': 0, 'max(x)': 30})
# __checkpoint_end__


# __predictor_start__
from ray.train.xgboost import XGBoostPredictor

test_dataset = ray.data.from_items([{"x": x} for x in range(2, 32, 3)])


class XGBoostPredictorWrapper:
    def __init__(self, checkpoint):
        self.predictor = XGBoostPredictor.from_checkpoint(checkpoint)

    def __call__(self, batch):
        return self.predictor.predict(batch)


predicted_probabilities = test_dataset.map_batches(
    XGBoostPredictorWrapper,
    compute=ray.data.ActorPoolStrategy(size=2),
    fn_constructor_kwargs={"checkpoint": checkpoint},
    batch_format="pandas",
)
predicted_probabilities.show()
# {'predictions': 0.09843720495700836}
# {'predictions': 5.604666709899902}
# {'predictions': 11.405311584472656}
# {'predictions': 15.684700012207031}
# {'predictions': 23.990947723388672}
# {'predictions': 29.900211334228516}
# {'predictions': 34.59944152832031}
# {'predictions': 40.6968994140625}
# {'predictions': 45.68107604980469}
# __predictor_end__


# __chain_start__
import ray
from ray.data.preprocessors import Chain, MinMaxScaler, SimpleImputer

# Generate one simple dataset.
dataset = ray.data.from_items(
    [{"id": 0}, {"id": 1}, {"id": 2}, {"id": 3}, {"id": None}]
)
print(dataset.take())
# [{'id': 0}, {'id': 1}, {'id': 2}, {'id': 3}, {'id': None}]

preprocessor = Chain(SimpleImputer(["id"]), MinMaxScaler(["id"]))

dataset_transformed = preprocessor.fit_transform(dataset)
print(dataset_transformed.take())
# [{'id': 0.0}, {'id': 0.3333333333333333}, {'id': 0.6666666666666666}, {'id': 1.0}, {'id': 0.5}]
# __chain_end__


# __custom_stateless_start__
import ray
from ray.data.preprocessors import BatchMapper

# Generate a simple dataset.
dataset = ray.data.range(4)
print(dataset.take())
# [{'id': 0}, {'id': 1}, {'id': 2}, {'id': 3}]

# Create a stateless preprocess that multiplies ids by 2.
preprocessor = BatchMapper(lambda df: df * 2, batch_size=2, batch_format="pandas")
dataset_transformed = preprocessor.transform(dataset)
print(dataset_transformed.take())
# [{'id': 0}, {'id': 2}, {'id': 4}, {'id': 6}]
# __custom_stateless_end__


# __custom_stateful_start__
from typing import Dict
import ray
from pandas import DataFrame
from ray.data.preprocessor import Preprocessor
from ray.data import Dataset
from ray.data.aggregate import Max


class CustomPreprocessor(Preprocessor):
    def _fit(self, dataset: Dataset) -> Preprocessor:
        self.stats_ = dataset.aggregate(Max("id"))

    def _transform_pandas(self, df: DataFrame) -> DataFrame:
        return df * self.stats_["max(id)"]


# Generate a simple dataset.
dataset = ray.data.range(4)
print(dataset.take())
# [{'id': 0}, {'id': 1}, {'id': 2}, {'id': 3}]

# Create a stateful preprocessor that finds the max id and scales each id by it.
preprocessor = CustomPreprocessor()
dataset_transformed = preprocessor.fit_transform(dataset)
print(dataset_transformed.take())
# [{'id': 0}, {'id': 3}, {'id': 6}, {'id': 9}]
# __custom_stateful_end__


# __simple_imputer_start__
from ray.data.preprocessors import SimpleImputer

# Generate a simple dataset.
dataset = ray.data.from_items([{"id": 1.0}, {"id": None}, {"id": 3.0}])
print(dataset.take())
# [{'id': 1.0}, {'id': None}, {'id': 3.0}]

imputer = SimpleImputer(columns=["id"], strategy="mean")
dataset_transformed = imputer.fit_transform(dataset)
print(dataset_transformed.take())
# [{'id': 1.0}, {'id': 2.0}, {'id': 3.0}]
# __simple_imputer_end__


# __concatenate_start__
from ray.data.preprocessors import Chain, Concatenator, StandardScaler

# Generate a simple dataset.
dataset = ray.data.from_items([{"X": 1.0, "Y": 2.0}, {"X": 4.0, "Y": 0.0}])
print(dataset.take())
# [{'X': 1.0, 'Y': 2.0}, {'X': 4.0, 'Y': 0.0}]

preprocessor = Chain(StandardScaler(columns=["X", "Y"]), Concatenator())
dataset_transformed = preprocessor.fit_transform(dataset)
print(dataset_transformed.take())
# [{'concat_out': array([-1.,  1.])}, {'concat_out': array([ 1., -1.])}]
# __concatenate_end__
