from abc import abstractmethod

import gym
import torch
import numpy as np

from grl.utils.log import log


class MinariDataset(torch.utils.data.Dataset):
    """
    Overview:
        Minari Dataset for QGPO && SRPOAlgorithm algorithm. The training of QGPO && SRPOAlgorithm algorithm is based on contrastive energy prediction, \
        which needs true action and fake action. The true action is sampled from the dataset, and the fake action \
        is sampled from the action support generated by the behaviour policy.
    Interface:
        ``__init__``, ``__getitem__``, ``__len__``.
    """

    def __init__(
        self,
        env_id: str,
        device: str = None,
    ):
        """
        Overview:
            Initialization method of MinariDataset class
        Arguments:
            env_id (:obj:`str`): The environment id
            device (:obj:`str`): The device to store the dataset
        """

        super().__init__()
        import minari
        import d4rl

        device = "cpu" if device is None else device
        dataset = minari.load_dataset(env_id)
        # merge the episodes
        tmp_dataset = {
            "observations": np.zeros((0, dataset[0].observations.shape[1])),
            "actions": np.zeros((0, dataset[0].actions.shape[1])),
            "rewards": np.zeros(0),
            "terminals": np.zeros(0),
        }
        for episode in range(dataset.__len__()):

            tmp_dataset["observations"] = np.vstack(
                (tmp_dataset["observations"], dataset[episode].observations[:-1])
            )
            tmp_dataset["actions"] = np.vstack(
                (tmp_dataset["actions"], dataset[episode].actions)
            )
            tmp_dataset["rewards"] = np.hstack(
                (tmp_dataset["rewards"], dataset[episode].rewards)
            )
            tmp_dataset["terminals"] = np.hstack(
                (tmp_dataset["terminals"], dataset[episode].terminations)
            )

        env = dataset.recover_environment()
        data = d4rl.qlearning_dataset(env, tmp_dataset)

        self.states = torch.from_numpy(data["observations"]).float().to(device)
        self.actions = torch.from_numpy(data["actions"]).float().to(device)
        self.next_states = (
            torch.from_numpy(data["next_observations"]).float().to(device)
        )
        reward = torch.from_numpy(data["rewards"]).view(-1, 1).float().to(device)
        self.is_finished = (
            torch.from_numpy(data["terminals"]).view(-1, 1).float().to(device)
        )

        reward_tune = "iql_antmaze" if "antmaze" in env_id else "iql_locomotion"
        if reward_tune == "normalize":
            reward = (reward - reward.mean()) / reward.std()
        elif reward_tune == "iql_antmaze":
            reward = reward - 1.0
        elif reward_tune == "iql_locomotion":
            min_ret, max_ret = MinariDataset.return_range(data, 1000)
            reward /= max_ret - min_ret
            reward *= 1000
        elif reward_tune == "cql_antmaze":
            reward = (reward - 0.5) * 4.0
        elif reward_tune == "antmaze":
            reward = (reward - 0.25) * 2.0
        self.rewards = reward
        self.len = self.states.shape[0]
        log.info(f"{self.len} data loaded in MinariDataset")

    def __getitem__(self, index):
        """
        Overview:
            Get data by index
        Arguments:
            index (:obj:`int`): Index of data
        Returns:
            data (:obj:`dict`): Data dict
        
        .. note::
            The data dict contains the following keys:
            
            s (:obj:`torch.Tensor`): State
            a (:obj:`torch.Tensor`): Action
            r (:obj:`torch.Tensor`): Reward
            s_ (:obj:`torch.Tensor`): Next state
            d (:obj:`torch.Tensor`): Is finished
            fake_a (:obj:`torch.Tensor`): Fake action for contrastive energy prediction and qgpo training \
                (fake action is sampled from the action support generated by the behaviour policy)
            fake_a_ (:obj:`torch.Tensor`): Fake next action for contrastive energy prediction and qgpo training \
                (fake action is sampled from the action support generated by the behaviour policy)
        """

        data = {
            "s": self.states[index % self.len],
            "a": self.actions[index % self.len],
            "r": self.rewards[index % self.len],
            "s_": self.next_states[index % self.len],
            "d": self.is_finished[index % self.len],
            "fake_a": (
                self.fake_actions[index % self.len]
                if hasattr(self, "fake_actions")
                else 0.0
            ),  # self.fake_actions <D, 16, A>
            "fake_a_": (
                self.fake_next_actions[index % self.len]
                if hasattr(self, "fake_next_actions")
                else 0.0
            ),  # self.fake_next_actions <D, 16, A>
        }
        return data

    def __len__(self):
        return self.len

    @abstractmethod
    def return_range(self, dataset, max_episode_steps):
        raise NotImplementedError

    def return_range(dataset, max_episode_steps):
        returns, lengths = [], []
        ep_ret, ep_len = 0.0, 0
        for r, d in zip(dataset["rewards"], dataset["terminals"]):
            ep_ret += float(r)
            ep_len += 1
            if d or ep_len == max_episode_steps:
                returns.append(ep_ret)
                lengths.append(ep_len)
                ep_ret, ep_len = 0.0, 0
        # returns.append(ep_ret)    # incomplete trajectory
        lengths.append(ep_len)  # but still keep track of number of steps
        assert sum(lengths) == len(dataset["rewards"])
        return min(returns), max(returns)
