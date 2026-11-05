"""Wait for the real async vehicle action, including cold module loading."""


def await_client_vehicle_action(page, selector="[data-client-vehicle-save]"):
    button = page.locator(selector).first
    button.wait_for(state="visible")
    if not button.is_enabled():
        raise AssertionError("vehicle action is disabled")
    button.evaluate(
        """async target => {
          await handleClientVehiclesClick({target, preventDefault() {}});
        }"""
    )
