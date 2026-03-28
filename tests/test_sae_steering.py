import math

from sae.model import make_identity_sae
from sae.steer import SteeringConfig, apply_steering_to_hidden


def _row_norm(row):
    return math.sqrt(sum(value * value for value in row))


def test_apply_steering_changes_last_token_norm_only():
    hidden = [
        [0.1, 0.2, 0.3, 0.4],
        [0.5, 0.6, 0.7, 0.8],
    ]
    sae = make_identity_sae(d_model=4, n_features=4)
    config = SteeringConfig(features={1: +0.8}, layer=12, token_positions="last")

    steered, report = apply_steering_to_hidden(
        hidden_states=hidden,
        decoder=sae.decoder,
        config=config,
        layer=12,
    )

    assert steered[0] == hidden[0]
    assert steered[1][1] == hidden[1][1] + 0.8
    assert _row_norm(steered[1]) > _row_norm(hidden[1])
    assert report["applied"] is True
    assert report["positions"] == [1]
    assert report["delta_l2"] > 0.0


def test_apply_steering_dry_run_keeps_hidden_unchanged():
    hidden = [
        [0.1, 0.2, 0.3],
        [0.4, 0.5, 0.6],
    ]
    sae = make_identity_sae(d_model=3, n_features=3)
    config = SteeringConfig(features={0: -0.3}, layer=3, token_positions="all", dry_run=True)

    steered, report = apply_steering_to_hidden(
        hidden_states=hidden,
        decoder=sae.decoder,
        config=config,
        layer=3,
    )

    assert steered == hidden
    assert report["applied"] is False
    assert report["reason"] == "dry_run"
    assert report["positions"] == [0, 1]
