"""Unique-pixel accounting for cache, model and prepared display references.

QImage copies share a cacheKey until detached. References are counted by owner;
the backing pixels are charged once, without scanning the listing on completion.
"""
class BrowserArtifactMemory:
    def __init__(self):
        self.owners = {}
        self.pixels = {}
        self.bytes = 0
        self.display_bytes = 0
        self.role_bytes = {}

    def retain(self, owner, image):
        pixel = int(image.cacheKey())
        if self.owners.get(owner) == pixel:
            return
        self.release(owner)
        role = owner[0]
        cost, refs, roles = self.pixels.setdefault(pixel, (int(image.sizeInBytes()), set(), {}))
        if not refs:
            self.bytes += cost
        if not roles.get(role):
            self.role_bytes[role] = self.role_bytes.get(role, 0) + cost
        if role != 'cache' and not any(r != 'cache' for r in roles):
            self.display_bytes += cost
        refs.add(owner)
        roles[role] = roles.get(role, 0) + 1
        self.owners[owner] = pixel

    def release(self, owner):
        pixel = self.owners.pop(owner, None)
        if pixel is None:
            return
        cost, refs, roles = self.pixels[pixel]
        role = owner[0]
        refs.remove(owner)
        roles[role] -= 1
        if not roles[role]:
            del roles[role]
            self.role_bytes[role] -= cost
        if role != 'cache' and not any(r != 'cache' for r in roles):
            self.display_bytes -= cost
        if not refs:
            self.bytes -= cost
            del self.pixels[pixel]


class ThumbnailImageReferences(dict):
    """Model image dictionary with incremental ownership notifications."""
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def __setitem__(self, key, image):
        super().__setitem__(key, image)
        self.callback(key, image)

    def pop(self, key, default=None):
        if key not in self:
            return default
        image = super().pop(key)
        self.callback(key, None)
        return image

    def __delitem__(self, key):
        if key not in self:
            raise KeyError(key)
        self.pop(key)

    def update(self, images=(), **kwargs):
        for key, image in dict(images, **kwargs).items():
            self[key] = image

    def clear(self):
        for key in tuple(self):
            self.pop(key)

    def replace(self, images):
        for key in tuple(self):
            if key not in images:
                self.pop(key)
        for key, image in images.items():
            self[key] = image
