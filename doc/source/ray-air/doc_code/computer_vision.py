def main():
    for framework in "torch", "tensorflow":
        for datasource in "tfrecords", "images", "numpy", "parquet":
            test(framework=framework, datasource=datasource)


def test(*, framework: str, datasource: str):
    assert framework in {"torch", "tensorflow"}
    assert datasource in {"tfrecords", "images", "numpy", "parquet"}

    if datasource == "tfrecords":
        dataset = read_tfrecords()
    if datasource == "images":
        dataset = read_images()
    if datasource == "numpy":
        dataset = read_numpy()
    if datasource == "parquet":
        dataset = read_parquet()

    dataset = dataset.limit(32)

    if framework == "torch":
        preprocessor, per_epoch_preprocessor = create_torch_preprocessors()
        train_torch_model(dataset, preprocessor, per_epoch_preprocessor)
        checkpoint = create_torch_checkpoint(preprocessor)
        online_predict_torch(checkpoint)
    if framework == "tensorflow":
        preprocessor, per_epoch_preprocessor = create_tensorflow_preprocessors()
        train_tensorflow_model(dataset, preprocessor, per_epoch_preprocessor)
        checkpoint = create_tensorflow_checkpoint(preprocessor)
        online_predict_tensorflow(checkpoint)


def read_tfrecords():
    # __read_tfrecords1_start__
    import ray

    dataset = ray.data.read_tfrecords(
        "s3://anonymous@air-example-data/cifar-10/tfrecords"
    )
    # __read_tfrecords1_stop__

    # __read_tfrecords2_start__
    import io
    from typing import Dict

    import numpy as np
    from PIL import Image

    def decode_bytes(batch: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
        images = []
        for data in batch["image"]:
            image = Image.open(io.BytesIO(data))
            images.append(np.array(image))
        batch["image"] = np.array(images)
        return batch

    dataset = dataset.map_batches(decode_bytes, batch_format="numpy")
    # __read_tfrecords2_stop__
    return dataset


def read_numpy():
    # __read_numpy1_start__
    import ray

    images = ray.data.read_numpy("s3://anonymous@air-example-data/cifar-10/images.npy")
    labels = ray.data.read_numpy("s3://anonymous@air-example-data/cifar-10/labels.npy")
    # __read_numpy1_stop__

    # __read_numpy2_start__
    dataset = images.zip(labels)
    dataset = dataset.map_batches(
        lambda batch: batch.rename(columns={"data": "image", "data_1": "label"}),
        batch_format="pandas",
    )
    # __read_numpy2_stop__
    return dataset


def read_parquet():
    # __read_parquet_start__
    import ray

    dataset = ray.data.read_parquet("s3://anonymous@air-example-data/cifar-10/parquet")
    # __read_parquet_stop__
    return dataset


def read_images():
    # __read_images1_start__
    import ray
    from ray.data.datasource.partitioning import Partitioning

    root = "s3://anonymous@air-example-data/cifar-10/images"
    partitioning = Partitioning("dir", field_names=["class"], base_dir=root)
    dataset = ray.data.read_images(root, partitioning=partitioning)
    # __read_images1_stop__

    dataset = dataset.limit(32)

    # __read_images2_start__
    from typing import Dict

    import numpy as np

    CLASS_TO_LABEL = {
        "airplane": 0,
        "automobile": 1,
        "bird": 2,
        "cat": 3,
        "deer": 4,
        "dog": 5,
        "frog": 6,
        "horse": 7,
        "ship": 8,
        "truck": 9,
    }

    def add_label_column(batch: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
        labels = []
        for name in batch["class"]:
            label = CLASS_TO_LABEL[name]
            labels.append(label)
        batch["label"] = np.array(labels)
        return batch

    def remove_class_column(batch: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
        del batch["class"]
        return batch

    dataset = dataset.map_batches(add_label_column).map_batches(remove_class_column)
    # __read_images2_stop__
    return dataset


def create_torch_preprocessors():
    # __torch_preprocessors_start__
    from torchvision import transforms

    from ray.data.preprocessors import TorchVisionPreprocessor

    transform = transforms.Compose([transforms.ToTensor(), transforms.CenterCrop(224)])
    preprocessor = TorchVisionPreprocessor(columns=["image"], transform=transform)

    per_epoch_transform = transforms.RandomHorizontalFlip(p=0.5)
    per_epoch_preprocessor = TorchVisionPreprocessor(
        columns=["image"], transform=per_epoch_transform
    )
    # __torch_preprocessors_stop__
    return preprocessor, per_epoch_preprocessor


def create_tensorflow_preprocessors():
    # __tensorflow_preprocessors_start__
    from typing import Dict

    import numpy as np
    import tensorflow as tf
    from tensorflow.keras.applications import imagenet_utils

    from ray.data.preprocessors import BatchMapper

    def preprocess(batch: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
        batch["image"] = imagenet_utils.preprocess_input(batch["image"])
        batch["image"] = tf.image.resize(batch["image"], (224, 224)).numpy()
        return batch

    preprocessor = BatchMapper(preprocess, batch_format="numpy")

    def augment(batch: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
        batch["image"] = tf.image.random_flip_left_right(batch["image"]).numpy()
        return batch

    per_epoch_preprocessor = BatchMapper(augment, batch_format="numpy")
    # __tensorflow_preprocessors_stop__
    return preprocessor, per_epoch_preprocessor


def train_torch_model(dataset, preprocessor, per_epoch_preprocessor):
    # __torch_training_loop_start__
    import torch.nn as nn
    import torch.optim as optim
    from torchvision import models

    from ray import train
    from ray.train import ScalingConfig
    from ray.train.torch import TorchCheckpoint, TorchTrainer

    def train_one_epoch(model, *, criterion, optimizer, batch_size, epoch):
        dataset_shard = train.get_dataset_shard("train")

        running_loss = 0
        for i, batch in enumerate(
            dataset_shard.iter_torch_batches(
                batch_size=batch_size, local_shuffle_buffer_size=256
            )
        ):
            inputs, labels = batch["image"], batch["label"]

            outputs = model(inputs)
            loss = criterion(outputs, labels)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            running_loss += loss.item()
            if i % 2000 == 1999:
                train.report(
                    metrics={
                        "epoch": epoch,
                        "batch": i,
                        "running_loss": running_loss / 2000,
                    },
                    checkpoint=TorchCheckpoint.from_model(model),
                )
                running_loss = 0

    def train_loop_per_worker(config):
        model = train.torch.prepare_model(models.resnet50())
        criterion = nn.CrossEntropyLoss()
        optimizer = optim.SGD(model.parameters(), lr=config["lr"])

        for epoch in range(config["epochs"]):
            train_one_epoch(
                model,
                criterion=criterion,
                optimizer=optimizer,
                batch_size=config["batch_size"],
                epoch=epoch,
            )

    # __torch_training_loop_stop__

    # __torch_trainer_start__
    dataset = per_epoch_preprocessor.transform(dataset)
    trainer = TorchTrainer(
        train_loop_per_worker=train_loop_per_worker,
        train_loop_config={"batch_size": 32, "lr": 0.02, "epochs": 1},
        datasets={"train": dataset},
        scaling_config=ScalingConfig(num_workers=2),
        preprocessor=preprocessor,
    )
    results = trainer.fit()
    # __torch_trainer_stop__
    return results


def train_tensorflow_model(dataset, preprocessor, per_epoch_preprocessor):
    # __tensorflow_training_loop_start__
    import tensorflow as tf

    from ray import train
    from ray.air.integrations.keras import ReportCheckpointCallback

    def train_loop_per_worker(config):
        strategy = tf.distribute.experimental.MultiWorkerMirroredStrategy()

        train_shard = train.get_dataset_shard("train")
        train_dataset = train_shard.to_tf(
            "image",
            "label",
            batch_size=config["batch_size"],
            local_shuffle_buffer_size=256,
        )

        with strategy.scope():
            model = tf.keras.applications.resnet50.ResNet50(weights=None)
            optimizer = tf.keras.optimizers.Adam(config["lr"])
            model.compile(
                optimizer=optimizer,
                loss="sparse_categorical_crossentropy",
                metrics=["accuracy"],
            )

        model.fit(
            train_dataset,
            epochs=config["epochs"],
            callbacks=[ReportCheckpointCallback()],
        )

    # __tensorflow_training_loop_stop__

    # __tensorflow_trainer_start__
    from ray.train import ScalingConfig
    from ray.train.tensorflow import TensorflowTrainer

    # The following transform operation is lazy.
    # It will be re-run every epoch.
    dataset = per_epoch_preprocessor.transform(dataset)
    trainer = TensorflowTrainer(
        train_loop_per_worker=train_loop_per_worker,
        train_loop_config={"batch_size": 32, "lr": 0.02, "epochs": 1},
        datasets={"train": dataset},
        scaling_config=ScalingConfig(num_workers=2),
        preprocessor=preprocessor,
    )
    results = trainer.fit()
    # __tensorflow_trainer_stop__
    return results


def create_torch_checkpoint(preprocessor):
    # __torch_checkpoint_start__
    from torchvision import models

    from ray.train.torch import TorchCheckpoint

    model = models.resnet50(pretrained=True)
    checkpoint = TorchCheckpoint.from_model(model, preprocessor=preprocessor)
    # __torch_checkpoint_stop__
    return checkpoint


def create_tensorflow_checkpoint(preprocessor):
    # __tensorflow_checkpoint_start__
    import tensorflow as tf

    from ray.train.tensorflow import TensorflowCheckpoint

    model = tf.keras.applications.resnet50.ResNet50()
    checkpoint = TensorflowCheckpoint.from_model(model, preprocessor=preprocessor)
    # __tensorflow_checkpoint_stop__
    return checkpoint


def online_predict_torch(checkpoint):
    # __torch_serve_start__
    from io import BytesIO
    import numpy as np
    from PIL import Image
    from ray import serve
    from ray.train.torch import TorchPredictor

    @serve.deployment
    class TorchDeployment:
        def __init__(self, checkpoint):
            self.predictor = TorchPredictor.from_checkpoint(checkpoint)

        async def __call__(self, request):
            image = Image.open(BytesIO(await request.body()))
            return self.predictor.predict(np.array(image)[np.newaxis])

    serve.run(TorchDeployment.bind(checkpoint))
    # __torch_serve_stop__

    # __torch_online_predict_start__
    import requests

    response = requests.get("http://placekitten.com/200/300")
    response = requests.post("http://localhost:8000/", data=response.content)
    predictions = response.json()
    # __torch_online_predict_stop__
    predictions


def online_predict_tensorflow(checkpoint):
    # __tensorflow_serve_start__
    from io import BytesIO
    import numpy as np
    from PIL import Image
    import tensorflow as tf

    from ray import serve
    from ray.train.tensorflow import TensorflowPredictor

    @serve.deployment
    class TensorflowDeployment:
        def __init__(self, checkpoint):
            self.predictor = TensorflowPredictor.from_checkpoint(
                checkpoint,
                model_definition=tf.keras.applications.resnet50.ResNet50,
            )

        async def __call__(self, request):
            image = Image.open(BytesIO(await request.body()))
            return self.predictor.predict({"image": np.array(image)[np.newaxis]})

    serve.run(TensorflowDeployment.bind(checkpoint))
    # __tensorflow_serve_stop__

    # __tensorflow_online_predict_start__
    import requests

    response = requests.get("http://placekitten.com/200/300")
    response = requests.post("http://localhost:8000/", data=response.content)
    predictions = response.json()
    # __tensorflow_online_predict_stop__
    predictions


if __name__ == "__main__":
    main()
