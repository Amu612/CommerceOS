variable "name" {
  type        = string
  description = "Prefix for repository names, e.g. \"commerceos\" -> commerceos/api"
}

variable "repository_names" {
  type        = list(string)
  description = "Repository suffixes to create, e.g. [\"api\", \"frontend\"]"
  default     = ["api", "frontend"]
}

variable "keep_last_n" {
  type    = number
  default = 15
}

variable "tags" {
  type    = map(string)
  default = {}
}
