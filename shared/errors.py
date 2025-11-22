class CustomError(Exception):
    def __init__(self, message, status_code=500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class ValidationError(CustomError):
    def __init__(self, message):
        super().__init__(message, 400)

class NotFoundError(CustomError):
    def __init__(self, message):
        super().__init__(message, 404)

class UnauthorizedError(CustomError):
    def __init__(self, message):
        super().__init__(message, 401)

class ConflictError(CustomError):
    def __init__(self, message):
        super().__init__(message, 409)
