from typing import Optional, List, Tuple, Callable, Union, Type

import gym
import torch as th
import torch.nn as nn
import numpy as np

from stable_baselines3.common.preprocessing import get_action_dim, get_obs_dim
from stable_baselines3.common.policies import BasePolicy, register_policy, create_mlp


class Q_Net(BasePolicy):
    """
    Class with a Q-Value Net for DQN

    :param observation_space: (gym.spaces.Space) Observation space
    :param action_space: (gym.spaces.Space) Action space
    :param lr_schedule: (callable) Learning rate schedule (could be constant)
    :param net_arch: (Optional[List[int]]) The specification of the policy and value networks.
    :param device: (str or th.device) Device on which the code should run.
    :param use_sde: (str or th.device) sde param that has to be here because of base_class implementation
    :param activation_fn: (Type[nn.Module]) Activation function
    :param log_std_init: (float) Initial value for the log standard deviation
    :param epsilon: (float) Epsilon for greedy policy
    :param normalize_images: (bool) Whether to normalize images or not,
         dividing by 255.0 (True by default)
    """

    def __init__(self, observation_space: gym.spaces.Space,
                 action_space: gym.spaces.Space,
                 features_extractor: nn.Module,
                 features_dim: int,
                 action_dim: int,
                 net_arch: Optional[List[int]] = None,
                 device: Union[th.device, str] = 'cpu',
                 activation_fn: Type[nn.Module] = nn.ReLU,
                 epsilon: float = 0.05,
                 normalize_images: bool = True):
        super(Q_Net, self).__init__(observation_space, action_space, device)

        if net_arch is None:
            net_arch = [64, 64]

        self.net_arch = net_arch
        self.activation_fn = activation_fn
        self.features_extractor = features_extractor
        self.features_dim = features_dim
        self.action_dim = action_dim
        self.normalize_images = normalize_images
        # Setup initial learning rate of the policy
        self.epsilon = epsilon

        q_net = create_mlp(self.features_dim, self.action_dim, self.net_arch, self.activation_fn)
        self.q_net = nn.Sequential(*q_net)

    def forward(self, obs: th.Tensor) -> th.Tensor:
        """
        Forward pass
        :param obs: (th.Tensor) Observation
        """
        features = self.extract_features(obs)
        return self.q_net(features)

    def predict(self, observation: th.Tensor, deterministic: bool = False) -> th.Tensor:
        """
        Get the action according to the policy for a given observation.

        :param observation: (th.Tensor)
        :param deterministic: (bool) Whether to use stochastic or deterministic actions
        :return: (th.Tensor) Taken action according to the policy
        """
        # epsilon greedy exploration
        if not deterministic and np.random.rand() < self.epsilon:
            if observation.ndim > 1:
                action = th.tensor([self.action_space.sample() for i in range(observation.shape[0])]).reshape(1)
            else:
                action = th.tensor(self.action_space.sample()).reshape(1)

        else:
            features = self.extract_features(observation)
            q_val = self.q_net(features)
            action = th.argmax(q_val, 1).reshape(-1)

        return action


class DQNPolicy(BasePolicy):
    """
    Policy class with Q-Value Net and target net for DQN

    :param observation_space: (gym.spaces.Space) Observation space
    :param action_space: (gym.spaces.Space) Action space
    :param lr_schedule: (callable) Learning rate schedule (could be constant)
    :param net_arch: (Optional[List[int]]) The specification of the policy and value networks.
    :param device: (str or th.device) Device on which the code should run.
    :param use_sde: (str or th.device) sde param that has to be here because of base_class implementation
    :param activation_fn: (Type[nn.Module]) Activation function
    :param log_std_init: (float) Initial value for the log standard deviation
    :param epsilon: (float) Epsilon for greedy policy
    :param normalize_images: (bool) Whether to normalize images or not,
         dividing by 255.0 (True by default)
    """

    def __init__(self, observation_space: gym.spaces.Space,
                 action_space: gym.spaces.Space,
                 lr_schedule: Callable,
                 net_arch: Optional[List[int]] = None,
                 device: Union[th.device, str] = 'cpu',
                 use_sde: bool = False,
                 activation_fn: Type[nn.Module] = nn.ReLU,
                 log_std_init: float = 0.0,
                 epsilon: float = 0.05,
                 normalize_images: bool = True):
        super(DQNPolicy, self).__init__(observation_space, action_space, device)

        if net_arch is None:
            net_arch = [256, 256]

        self.net_arch = net_arch
        self.activation_fn = activation_fn
        # In the future, features_extractor will be replaced with a CNN
        self.features_extractor = nn.Flatten()
        self.features_dim = get_obs_dim(self.observation_space)
        self.action_dim = self.action_space.n  # number of actions
        self.normalize_images = normalize_images
        self.epsilon = epsilon

        self.net_args = {
            'observation_space': self.observation_space,
            'action_space': self.action_space,
            'features_extractor': self.features_extractor,
            'features_dim': self.features_dim,
            'action_dim': self.action_dim,
            'net_arch': self.net_arch,
            'epsilon': self.epsilon,
            'activation_fn': self.activation_fn,
            'normalize_images': normalize_images,
            'device': device
        }

        self.log_std_init = log_std_init  # Not used by now, only discrete env supported

        self.q_net, self.q_net_target = None, None

        self._build(lr_schedule)

    def _build(self, lr_schedule: Callable) -> None:
        """
        Create the network and the optimizer.

        :param lr_schedule: (Callable) Learning rate schedule
            lr_schedule(1) is the initial learning rate
        """

        self.q_net = self.make_q_net()
        self.q_net_target = self.make_q_net()
        self.q_net_target.load_state_dict(self.q_net.state_dict())

        # Setup optimizer with initial learning rate
        self.optimizer = th.optim.Adam(self.parameters(), lr=lr_schedule(1))

    def update_epsilon(self, epsilon: float):
        self.q_net_target.epsilon = epsilon
        self.q_net.epsilon = epsilon
        self.epsilon = epsilon

    def make_q_net(self) -> Q_Net:
        return Q_Net(**self.net_args).to(self.device)

    def q_forward(self, obs: th.Tensor) -> th.Tensor:
        return self.predict(obs, deterministic=False)

    def q_predict(self, observation: th.Tensor, deterministic: bool = False) -> th.Tensor:
        return self.q_net.predict(observation, deterministic)

    def _predict(self, observation: th.Tensor, deterministic: bool = False) -> th.Tensor:
        return self.q_net.predict(observation, deterministic)


MlpPolicy = DQNPolicy

register_policy("MlpPolicy", MlpPolicy)