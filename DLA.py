# Importing Libraries
import copy
import math
import torch
import numpy as np
import torch.optim as optim
from typing import Callable, Union, Literal


# Helper Functions
BCE_loss_instance = torch.nn.BCELoss()
def ModifiedBCELoss(y_pred, y):
    return -1 * BCE_loss_instance(y_pred,y)

# Main class
class DLA:
    def __init__(
        self,
        model_params: dict,
        train_params: dict,
        model_acc: float,
        eval_metric: Union[Callable, str] = "mse",
        threshold=True,
    ) -> None:
        """
        Parameters
        ----------
        model_params : dict
            Dictionary of the following forms-
            {"attacker_AtoT" : model_AT, "attacker_TtoA" : model_TA}
        train_params : dict
            {
                "AtoT":
                    {
                        "learning_rate": The learning rate hyperparameter,
                        "loss_function": The loss function to be used.
                                Existing options: ["mse", "cross-entropy"],
                        "epochs": Number of training epochs to be set,
                        "batch_size: Number of batches per epoch
                    },
                "TtoA": {same format as AtoT}
            }
        model_acc : float
            The accuracy of the model being tested for quality equalization.
        eval_metric : Union[Callable,str], optional
            Either a Callable of the form eval_metric(y_pred, y)
            or a string to utilize exiting methods.
            Existing options include ["accuracy"]
            The default is "mse".

        Returns
        -------
        None
            Initializes the class.

        """
        self.model_params = model_params
        self.train_params = train_params
        self.model_attacker_trained = False
        self.threshold = threshold
        self.model_acc = model_acc

        self.loss_functions = {
            "mse": torch.nn.MSELoss(),
            "cross-entropy": torch.nn.CrossEntropyLoss(),
            "bce": torch.nn.BCELoss(),
        }
        self.eval_functions = {
            "accuracy": lambda y_pred, y: (y_pred == y).float().mean(),
            "mse": lambda y_pred, y: ((y_pred - y) ** 2).float().mean(),
            "bce": ModifiedBCELoss,
        }
        self.initEvalMetric(eval_metric)
        self.defineModel()

    def calcLeak(
        self,
        feat: torch.tensor,
        data: torch.tensor,
        pred: torch.tensor,
        mode: Literal["AtoT", "TtoA"],
        normalized: bool = True,
    ) -> torch.tensor:
        """
        Parameters
        ----------
        feat : torch.tensor
            Protected Attribute.
        data : torch.tensor
            Ground truth data.
        pred : torch.tensor
            Predicted Values.
        mode : Literal["AtoT","TtoA"]
            Sets Direction of calculation.

        Returns
        -------
        leakage : torch.tensor
            Evaluated Leakage.

        """
        pert_data = self.permuteData(data)
        self.train(feat, pert_data, "D_" + mode)
        lambda_d = self.calcLambda(getattr(self, "attacker_D_" + mode), pert_data, feat)
        self.train(feat, pred, "M_" + mode)
        lambda_m = self.calcLambda(getattr(self, "attacker_M_" + mode), pred, feat)
        print(f"{lambda_d=},\n{lambda_m=}")
        leakage_amp = lambda_m - lambda_d
        if(normalized):
            leakage_amp = leakage_amp / (lambda_m + lambda_d)
        return leakage_amp

    def train(
        self,
        x: torch.tensor,
        y: torch.tensor,
        attacker_mode: str,
    ) -> torch.tensor:
        self.defineModel()
        model = getattr(self, "attacker_" + attacker_mode)
        criterion = self.loss_functions[self.train_params["loss_function"]]
        optimizer = optim.Adam(
            model.parameters(), lr=self.train_params["learning_rate"]
        )
        batches = math.ceil(len(x) / self.train_params["batch_size"])

        print(f"Training Activated for Mode: {attacker_mode}")

        # Training loop
        for epoch in range(1, self.train_params["epochs"] + 1):
            perm = torch.randperm(x.shape[0])
            x = x[perm]
            y = y[perm]
            start = 0
            running_loss = 0.0
            # print(batches)
            for batch_num in range(batches):
                x_batch = x[start : (start + self.train_params["batch_size"])]
                y_batch = y[start : (start + self.train_params["batch_size"])]

                optimizer.zero_grad()
                # Forward pass
                outputs = model(x_batch)
                # print(f"{outputs=}\n{y_batch=}")
                loss = criterion(outputs, y_batch)
                # print(f"{loss.item()=}")

                # Backward pass and optimization
                loss.backward()
                optimizer.step()

                start += self.train_params["batch_size"]
                running_loss += loss.item()

            avg_loss = running_loss / batches
            if epoch % 10 == 0:
                print(f"\rCurrent Epoch {epoch}: Loss = {avg_loss}", end="")

        print("\nModel training completed")

    def calcLambda(
        self, model: torch.nn.Module, x: torch.tensor, y: torch.tensor
    ) -> torch.tensor:
        y_pred = model(x)
        if self.threshold:
            y_pred = y_pred > 0.5
        return self.eval_metric(y_pred, y)

    def defineModel(self) -> None:
        if type(self.model_params.get("attacker_AtoT", None)) == None:
            raise Exception("attacker_AtoT Model Missing!")
        if type(self.model_params.get("attacker_TtoA", None)) == None:
            raise Exception("attacker_TtoA Model Missing!")
        self.attacker_D_AtoT = self.model_params["attacker_AtoT"]
        self.attacker_M_AtoT = copy.deepcopy(self.attacker_D_AtoT)
        self.attacker_D_TtoA = self.model_params["attacker_TtoA"]
        self.attacker_M_TtoA = copy.deepcopy(self.attacker_D_TtoA)

    def permuteData(self, data: torch.tensor) -> torch.tensor:
        """
        Currently assumes ground truth data to be binary values in a pytorch tensor.
        Should work for any NxM type array.

        Parameters
        ----------
        data : torch.tensor
            Original ground truth data.

        Returns
        -------
        new_data : torch.tensor
            Randomly pertubed data for quality equalization.
        """
        if self.model_acc > 1:
            self.model_acc = self.model_acc / 100
        num_observations = data.shape[0]
        rand_vect = torch.zeros((num_observations, 1))
        rand_vect[: int(self.model_acc * num_observations)] = 1
        rand_vect = rand_vect[torch.randperm(num_observations)]
        new_data = rand_vect * (data) + (1 - rand_vect) * (1 - data)
        return new_data

    def initEvalMetric(self, metric: Union[Callable, str]) -> None:
        if callable(metric):
            self.eval_metric = metric
        elif type(metric) == str:
            if metric in self.eval_functions.keys():
                self.eval_metric = self.eval_functions[metric]
            else:
                raise ValueError("Metric Option given is unavailable.")
        else:
            raise ValueError("Invalid Metric Given.")

    def getAmortizedLeakage(
        self,
        feat: torch.tensor,
        data: torch.tensor,
        pred: torch.tensor,
        mode: Literal["AtoT", "TtoA"],
        num_trials: int = 10,
        method: str = "mean",
        normalized: bool =  True,
    ) -> tuple[torch.tensor, torch.tensor]:
        vals = torch.zeros(num_trials)
        for i in range(num_trials):
            print(f"Working on Trial: {i}")
            vals[i] = self.calcLeak(feat, data, pred, mode, normalized)
            print(f"Trial {i} val: {vals[i]}")
        if method == "mean":
            return torch.mean(vals), torch.std(vals)
        elif method == "median":
            return torch.median(vals), torch.std(vals)
        else:
            raise ValueError("Invalid Method given for Amortization.")

    def calcBidirectional(
        self,
        A: torch.tensor,
        T: torch.tensor,
        A_pred: torch.tensor,
        T_pred: torch.tensor,
        num_trials: int = 10,
        method: str = "mean",
        normalized: bool = True,
    ) -> tuple[tuple[torch.tensor, torch.tensor], tuple[torch.tensor, torch.tensor]]:
        AtoT_vals = self.getAmortizedLeakage(A, T, T_pred, num_trials, method, normalized)
        TtoA_vals = self.getAmortizedLeakage(T, A, A_pred, num_trials, method, normalized)
        return (AtoT_vals, TtoA_vals)


if __name__ == "__main__":
    # Test case
    from attackerModels.ANN import simpleDenseModel

    # Data Initialization
    from utils.datacreator import dataCreator

    P, D, D2, M1, M2 = dataCreator(16384, 0.2, False, 0.05)
    P = torch.tensor(P, dtype=torch.float).reshape(-1, 1)
    D = torch.tensor(D, dtype=torch.float).reshape(-1, 1)
    D2 = torch.tensor(D2, dtype=torch.float).reshape(-1, 1)
    M1 = torch.tensor(M1, dtype=torch.float).reshape(-1, 1)
    M2 = torch.tensor(M2, dtype=torch.float).reshape(-1, 1)

    # Calculating Params
    model_1_acc = torch.sum(D == M1) / D.shape[0]
    model_2_acc = torch.sum(D == M2) / D.shape[0]

    # Parameter Initialization

    # Attacker Model Initialization
    attackerModel = simpleDenseModel(
        1, 1, 1, numFirst=1, activations=["sigmoid", "sigmoid", "sigmoid"]
    )

    # Parameter Initialization
    leakage_1 = DLA(
        {"attacker_AtoT": attackerModel, "attacker_TtoA": attackerModel},
        {
            "learning_rate": 0.05,
            "loss_function": "bce",
            "epochs": 100,
            "batch_size": 64,
        },
        model_1_acc,
        "accuracy",
        threshold=True,
    )

    leakage_2 = DLA(
        {"attacker_AtoT": attackerModel, "attacker_TtoA": attackerModel},
        {
            "learning_rate": 0.05,
            "loss_function": "bce",
            "epochs": 100,
            "batch_size": 64,
        },
        model_2_acc,
        "accuracy",
        threshold=True,
    )

    leak_1 = leakage_1.getAmortizedLeakage(P, D, M1, "AtoT")
    print(f"leakage for case 1: {leak_1}")
    print("______________________________________")
    print("______________________________________")
    leak_2 = leakage_2.getAmortizedLeakage(P, D, M2, "AtoT")
    print(f"leakage for case 2: {leak_2}")
    print("______________________________________")
    print("______________________________________")
    leak_3 = leakage_2.getAmortizedLeakage(P, D2, M1, "AtoT")
    print(f"leakage for case 3: {leak_3}")
    print("______________________________________")
    print("______________________________________")
    leak_4 = leakage_2.getAmortizedLeakage(P, D2, M2, "AtoT")
    print(f"leakage for case 4: {leak_4}")
    print("______________________________________")
    print("______________________________________")