/** Google Maps links - no API key needed. "Directions" opens Google Maps with the
 *  route from the person's current location to the theatre (on a phone it opens
 *  the Maps app). The theatre is found by name + city. */
const place = (theatre: string, city: string) => `${theatre}, ${city}`;

export const directionsUrl = (theatre: string, city: string) =>
  `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(place(theatre, city))}`;

export const mapEmbedUrl = (theatre: string, city: string) =>
  `https://www.google.com/maps?q=${encodeURIComponent(place(theatre, city))}&output=embed`;
