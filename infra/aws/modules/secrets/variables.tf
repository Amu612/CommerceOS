variable "prefix" {
  type        = string
  description = "Secret name prefix, e.g. \"commerceos/dev\""
}

variable "secrets" {
  type        = map(string)
  description = "Map of secret key -> initial value (sensitive)"
  sensitive   = true
}

variable "recovery_window_in_days" {
  type    = number
  default = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
