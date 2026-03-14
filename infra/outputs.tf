output "instance_ips" {
  description = "Public IPs of all CIM agent instances"
  value = {
    for i, instance in aws_instance.cim_agent :
    var.block_names[i] => instance.public_ip
  }
}

output "ssh_commands" {
  description = "SSH commands for each instance"
  value = {
    for i, instance in aws_instance.cim_agent :
    var.block_names[i] => "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${instance.public_ip}"
  }
}

output "launch_commands" {
  description = "Commands to launch the agent on each instance after SSH"
  value = {
    for i, instance in aws_instance.cim_agent :
    var.block_names[i] => "# On ${var.block_names[i]} instance:\n./launch_agent.sh ${var.block_names[i]}"
  }
}

output "instance_ids" {
  description = "Instance IDs for management"
  value = {
    for i, instance in aws_instance.cim_agent :
    var.block_names[i] => instance.id
  }
}
