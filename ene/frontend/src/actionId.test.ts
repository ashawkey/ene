import { newActionId } from './actionId'

describe('newActionId', () => {
  it('uses getRandomValues without requiring a secure-context randomUUID API', () => {
    const originalCrypto = globalThis.crypto
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.forEach((_, index) => { bytes[index] = index })
      return bytes
    })
    Object.defineProperty(globalThis, 'crypto', {
      configurable: true,
      value: { getRandomValues },
    })

    try {
      expect(newActionId()).toBe('000102030405060708090a0b0c0d0e0f')
      expect(getRandomValues).toHaveBeenCalledOnce()
    } finally {
      Object.defineProperty(globalThis, 'crypto', {
        configurable: true,
        value: originalCrypto,
      })
    }
  })
})
