import { PRODUCT } from "../lib/constants";
import Icon from "./Icon";

/** The standing statement about what AI output is and is not. */
export default function DisclaimerBanner() {
  return (
    <div className="notice notice--ai" role="note">
      <Icon name="cpu" size={18} />
      <p>
        <strong>AI generated · Preliminary.</strong> {PRODUCT.disclaimer}
      </p>
    </div>
  );
}
