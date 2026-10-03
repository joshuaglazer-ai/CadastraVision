import mark from "../assets/brand/cadastra-mark.png";

/** The product emblem from the Cadastra Vision logo: a drone over a parcel map. */
export default function BrandMark({ size = 28 }) {
  return (
    <img
      className="brand__mark brand__mark--image"
      src={mark}
      width={size}
      height={size}
      style={{ width: size, height: size }}
      alt=""
      aria-hidden="true"
    />
  );
}
