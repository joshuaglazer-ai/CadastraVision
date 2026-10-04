import { useLayoutEffect, useRef, useState } from "react";

/**
 * Height that lets an element fill the window from where it starts on the
 * page down to the bottom (less `bottom` px), never below `min`. Measured
 * from the document top, so scrolling does not change it.
 */
export default function useFillHeight({ min = 520, bottom = 24 } = {}) {
  const ref = useRef(null);
  const [height, setHeight] = useState(min);

  useLayoutEffect(() => {
    const measure = () => {
      const el = ref.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top + window.scrollY;
      setHeight(Math.max(min, Math.floor(window.innerHeight - top - bottom)));
    };
    measure();
    window.addEventListener("resize", measure);
    // Content above (the context strip, a notice) can change after load.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(document.body);
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [min, bottom]);

  return [ref, height];
}
