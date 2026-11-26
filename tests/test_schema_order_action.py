"""OrderAction uses Action/Organization properties and supplied commercial terms.

Property domains: https://schema.org/OrderAction,
https://schema.org/acceptedPaymentMethod and https://schema.org/EntryPoint.
The superseded merchant property belongs to Order, not OrderAction.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import schema_generate  # noqa: E402

ORDER_URL = "https://shop.example/order"
PICKUP = "https://schema.org/OnSitePickup"
PARCEL = "https://schema.org/ParcelService"


def test_merchant_is_the_action_provider_not_an_order_property():
    action = schema_generate.order_action("Acme shop", order_url=ORDER_URL)
    assert action["provider"] == {"@type": "Organization", "name": "Acme shop"}
    assert "merchant" not in action


@pytest.mark.parametrize("methods", [["Cash"], ["Visa", "Mastercard"], ["Bank transfer"]])
def test_payment_methods_belong_to_the_provider_organization(methods):
    action = schema_generate.order_action(
        "Acme shop", order_url=ORDER_URL, accepted_payment_method=methods,
    )
    assert "acceptedPaymentMethod" not in action
    assert action["provider"]["acceptedPaymentMethod"] == [
        {"@type": "PaymentMethod", "name": method} for method in methods
    ]


@pytest.mark.parametrize("optional", [None, []])
def test_unsupplied_terms_and_unsupported_entrypoint_language_are_omitted(optional):
    action = schema_generate.order_action(
        "Acme shop", order_url=ORDER_URL,
        accepted_payment_method=optional, delivery_method=optional,
    )
    assert "deliveryMethod" not in action
    assert "priceSpecification" not in action
    assert "price" not in action and "priceCurrency" not in action
    assert "inLanguage" not in action["target"]
    assert "acceptedPaymentMethod" not in action["provider"]


@pytest.mark.parametrize("methods", [[PICKUP], [PICKUP, PARCEL]])
def test_explicit_delivery_methods_are_preserved_without_adding_defaults(methods):
    action = schema_generate.order_action("Acme shop", order_url=ORDER_URL, delivery_method=methods)
    assert action["deliveryMethod"] == methods


def test_action_target_and_name_remain_available_for_potential_action():
    action = schema_generate.order_action("Acme shop", order_url=ORDER_URL, name="Reserve for pickup")
    assert action["@context"] == "https://schema.org" and action["@type"] == "OrderAction"
    assert action["name"] == "Reserve for pickup"
    assert action["target"]["@type"] == "EntryPoint"
    assert action["target"]["urlTemplate"] == ORDER_URL
    assert action["target"]["actionPlatform"] == [
        "https://schema.org/DesktopWebPlatform", "https://schema.org/MobileWebPlatform",
    ]


@pytest.mark.parametrize("script_tag", [False, True])
@pytest.mark.parametrize("supply_terms", [False, True])
def test_order_cli_emits_supported_nodes_in_json_and_script_output(script_tag, supply_terms):
    command = [sys.executable, str(SCRIPTS / "schema_generate.py")]
    if script_tag:
        command.append("--script-tag")
    command += ["order", "--merchant", "Acme shop", "--order-url", ORDER_URL]
    if supply_terms:
        command += ["--accepted-payment-method", "Cash", "Card", "--delivery-method", PICKUP]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0 and not result.stderr
    output = result.stdout
    if script_tag:
        assert output.startswith('<script type="application/ld+json">\n')
        assert output.endswith("</script>\n")
        output = output.split("\n", 1)[1].rsplit("</script>", 1)[0]
    action = json.loads(output)
    assert action["provider"]["name"] == "Acme shop"
    assert "merchant" not in action and "acceptedPaymentMethod" not in action
    assert "priceSpecification" not in action and "inLanguage" not in action["target"]
    if supply_terms:
        assert action["deliveryMethod"] == [PICKUP]
        assert [method["name"] for method in action["provider"]["acceptedPaymentMethod"]] == ["Cash", "Card"]
    else:
        assert "deliveryMethod" not in action and "acceptedPaymentMethod" not in action["provider"]
