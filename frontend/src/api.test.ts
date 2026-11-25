import { describe, expect, it } from 'vitest'
import { destinationFromInputs } from './api'

describe('destination coordinates', () => {
  it('preserves GCJ-02 longitude then latitude', () => {
    expect(destinationFromInputs('121.4737', '31.2304')).toEqual({ lng: 121.4737, lat: 31.2304 })
  })

  it('rejects swapped, blank and non-finite inputs', () => {
    expect(destinationFromInputs('31.2304', '121.4737')).toBeNull()
    expect(destinationFromInputs('121.4737', '')).toBeNull()
    expect(destinationFromInputs('Infinity', '31.2304')).toBeNull()
  })
})
