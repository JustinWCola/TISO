const paths = {
  pin: 'M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z M12 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z',
  layers: 'm12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5',
  chevron: 'm6 9 6 6 6-6',
  close: 'm6 6 12 12M18 6 6 18',
  eye: 'M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12Zm13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  eyeOff: 'm3 3 18 18M10 6.2c.6-.1 1.3-.2 2-.2 6.4 0 10 6 10 6a16 16 0 0 1-3.2 3.6M6 7.7A16 16 0 0 0 2 12s3.6 6 10 6c1.5 0 2.8-.3 4-.9',
  search: 'M20 20l-4-4M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z',
  plus: 'M12 5v14M5 12h14',
  minus: 'M5 12h14',
  center: 'M12 2v4m0 12v4M2 12h4m12 0h4M19 12a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 7v5l3 2',
  history: 'M3 12a9 9 0 1 1 2.7 6.4M3 6v6h6m3-5v5l3 2',
  check: 'M5 12l4 4L19 6',
  trash: 'M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5',
  car: 'M5 17v2m14-2v2M3 13l2-2 2-5h10l2 5 2 2v4H3v-4Zm2-2h14M7 14h1m8 0h1',
  transit: 'M6 18v2m12-2v2M4 15V7a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3Zm0-4h16M8 15h.01M16 15h.01',
  key: 'M15.5 3a5.5 5.5 0 0 0-5.2 7.3L3 17.6V21h3.4l1.7-1.7v-2h2v-2l2.1-2.1A5.5 5.5 0 1 0 15.5 3Zm2 5h.01',
}

export default function Icon({ name }: { name: keyof typeof paths }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]} /></svg>
}
