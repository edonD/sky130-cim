variable "project_name" {
  default = "sky130-cim"
}

variable "region" {
  default = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type — c6a.4xlarge = 16 vCPU, 32GB RAM, no GPU needed"
  default     = "c6a.4xlarge"
}

variable "volume_size" {
  description = "Root EBS volume size in GB"
  default     = 50
}

variable "key_name" {
  description = "Name of the EC2 key pair for SSH access"
  type        = string
}

variable "instance_count" {
  description = "Number of parallel instances to launch (3 for Phase 1)"
  default     = 3
}

variable "block_names" {
  description = "Block assigned to each instance (order matches instance index)"
  type        = list(string)
  default     = ["bitcell", "adc", "pwm-driver"]
}

variable "github_repo" {
  description = "GitHub repo URL for the CIM project"
  type        = string
  default     = ""
}
