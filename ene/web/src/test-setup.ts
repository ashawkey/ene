import '@testing-library/jest-dom/vitest'

// jsdom has no layout: scrolling to an element is a no-op.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
